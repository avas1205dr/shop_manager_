import telebot
import sqlite3
import json
import os
import shutil
import uuid
from datetime import datetime
from telebot import types
import requests
import threading
from yookassa import Configuration, Payment
import logging

logging.basicConfig(level=logging.ERROR)

user_product_messages = {}
active_shop_bots = {}

db_lock = threading.Lock()

# Инициализация бота
BOT_TOKEN = "7793591374:AAHYhGqYiNgg3EqKvSJFHsFxGCgpEKw7mgk"
bot = telebot.TeleBot(BOT_TOKEN)

# Настройки базы данных
DB_NAME = "db/shop_manager.db"

# Состояния для FSM
user_states = {}

class UserState:
    MAIN_MENU = "main_menu"
    CREATING_SHOP = "creating_shop"
    SHOP_MENU = "shop_menu"
    EDITING_TOKEN = "editing_token"
    EDITING_WELCOME = "editing_welcome"
    CREATING_CATEGORY = "creating_category"
    ADDING_PRODUCT = "adding_product"
    PRODUCT_NAME = "product_name"
    PRODUCT_PRICE = "product_price"
    PRODUCT_DESCRIPTION = "product_description"
    PRODUCT_IMAGE = "product_image"
    EDITING_PAYMENT = "editing_payment"
    ADDING_ADMIN = "adding_admin"
    GET_USER_ID = "get_user_id"
    EDITING_PRODUCT = "editing_product"
    WORKERS_MENU = "workers_menu"
    ADDING_WORKER = "adding_worker"
    REMOVING_WORKER = "removing_worker"
    EDITING_CATEGORY_NAME = "editing_category_name"
    DELETING_CATEGORY = "deleting_category"

class ShopBotState:
    MAIN_MENU = "main_menu"
    LEAVING_REVIEW = "leaving_review"
    REVIEW_RATING = "review_rating"
    REVIEW_TEXT = "review_text"
    VIEWING_CART = "viewing_cart"
    ENTERING_ADDRESS = "entering_address"
    EDITING_PRODUCT = "editing_product"
    BROWSE_CATEGORIES = "browse_categories"
    VIEW_PRODUCT = "view_product"
    SEARCH_MODE = "search_mode"
    FILTER_MODE = "filter_mode"
    VIEW_RECOMMENDATIONS = "view_recommendations"
    SEARCH_INPUT = "search_input"
    FILTER_MIN_PRICE = "filter_min_price"
    FILTER_MAX_PRICE = "filter_max_price"

# Инициализация базы данных
def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Таблица магазинов
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
        yookassa_credentials TEXT
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

    # Таблица администраторов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shop_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
    )
    ''')
    
    # Таблица категорий
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
    )
    ''')
    
    # Таблица товаров
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        image_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        popularity_score INTEGER DEFAULT 0,
        FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
    )
    ''')
    
    # Таблица отзывов
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
    
    # Таблица заказов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        customer_user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        delivery_address TEXT NOT NULL,
        payment_status TEXT DEFAULT 'pending',
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

    # Рекомендации
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
    conn.commit()
    
    # Проверка и добавление отсутствующих колонок
    cursor.execute("PRAGMA table_info(shops)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'is_running' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN is_running INTEGER DEFAULT 0")
    if 'yookassa_credentials' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN yookassa_credentials TEXT")
    if 'bot_username' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN bot_username TEXT")
    
    # Проверка и добавление колонки description в таблицу товаров
    cursor.execute("PRAGMA table_info(products)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'description' not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN description TEXT")
    
    conn.commit()
    conn.close()

Configuration.account_id = "YOUR_YOOKASSA_SHOP_ID"
Configuration.secret_key = "YOUR_YOOKASSA_SECRET_KEY"

# Функции для работы с базой данных

def send_edit_prompt(chat_id, prompt, back_callback):
    markup = create_back_button_menu(back_callback)
    bot.send_message(chat_id, prompt, reply_markup=markup)

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
    SELECT s.id, s.shop_name, s.bot_username, 
           COALESCE(AVG(r.rating), 0) AS avg_rating
    FROM shop_similarity sim
    JOIN shops s ON sim.similar_shop = s.id
    LEFT JOIN reviews r ON s.id = r.shop_id
    WHERE sim.source_shop = ?
    GROUP BY s.id
    ORDER BY sim.common_categories DESC, avg_rating DESC
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
    
    # Перезапуск бота
    if shop_id in active_shop_bots:
        try:
            active_shop_bots[shop_id].stop_polling()
        except:
            pass
        del active_shop_bots[shop_id]
    
    if bot_username:  # Запускаем только если токен валиден
        shop_info = get_shop_info(shop_id)
        threading.Thread(target=run_shop_bot, args=(shop_id, token, shop_info[5]), daemon=True).start()
        active_shop_bots[shop_id] = telebot.TeleBot(token)

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
    
    # Удаляем все товары в категории
    cursor.execute("SELECT id, image_path FROM products WHERE category_id = ?", (category_id,))
    products = cursor.fetchall()
    for product_id, image_path in products:
        # Удаляем изображения товаров
        if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
            try:
                os.remove(image_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении изображения товара: {e}")
    
    # Удаляем товары
    cursor.execute("DELETE FROM products WHERE category_id = ?", (category_id,))
    # Удаляем категорию
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

def add_product(category_id, name, price, image_path, description=None):
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
    cursor.execute("INSERT INTO products (category_id, name, price, image_path, description) VALUES (?, ?, ?, ?, ?)", 
                  (category_id, name, price, image_path, description))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id

def update_product(product_id, name=None, price=None, description=None, image_path=None):
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
    
    # Проверка, оставил ли пользователь уже отзыв
    cursor.execute("SELECT 1 FROM reviews WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
    if cursor.fetchone():
        conn.close()
        return False  # Отзыв уже существует
    
    # Добавление отзыва
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
    if not shop_info or not shop_info[9]:  # yookassa_credentials
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

def get_user_info_by_id(user_id):
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    try:
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
    if shop_info[1] == user_id:  # Создатель магазина
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
        cursor.execute("SELECT username FROM users WHERE tg_id = ?", (tg_id,))
        existing = cursor.fetchone()
        
        if not existing:
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

# Создание клавиатур
def create_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_reviews = types.InlineKeyboardButton("📊 Рейтинг", callback_data="reviews")
    btn_my_shops = types.InlineKeyboardButton("🏪 Мои магазины", callback_data="my_shops")
    markup.add(btn_reviews, btn_my_shops)
    return markup

def create_reviews_menu(page=0, per_page=5):
    markup = types.InlineKeyboardMarkup(row_width=1)
    shops = get_shops_with_ratings()
    
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(shops))
    
    for i in range(start_idx, end_idx):
        shop = shops[i]
        shop_id, shop_name, bot_username, avg_rating, review_count = shop
        avg_rating = float(avg_rating or 0)
        
        rating_stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
        
        btn_text = f"{shop_name}\n{rating_stars} ({review_count} отзывов)"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"shop_detail_{shop_id}"))
    
    # Кнопки пагинации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"reviews_page_{page-1}"))
    if end_idx < len(shops):
        nav_buttons.append(types.InlineKeyboardButton("Вперёд ➡️", callback_data=f"reviews_page_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return markup

def create_my_shops_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    shops = get_user_shops(user_id)
    
    for shop_id, shop_name in shops:
        markup.add(types.InlineKeyboardButton(shop_name, callback_data=f"manage_shop_{shop_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Создать новый магазин", callback_data="create_shop"))
    markup.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return markup

def create_shop_management_menu(shop_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_token = types.InlineKeyboardButton("🔑 API бота", callback_data=f"edit_token_{shop_id}")
    btn_products = types.InlineKeyboardButton("📦 Товары", callback_data=f"manage_products_{shop_id}")
    btn_all_products = types.InlineKeyboardButton("📦 Все товары", callback_data=f"all_products_{shop_id}")
    btn_workers = types.InlineKeyboardButton("👥 Работники", callback_data=f"workers_{shop_id}")
    btn_delete = types.InlineKeyboardButton("🗑️ Удалить магазин", callback_data=f"delete_shop_{shop_id}")
    btn_back = types.InlineKeyboardButton("⬅️ Назад", callback_data="my_shops")
    
    markup.add(btn_token)
    markup.add(btn_products, btn_all_products)
    markup.add(btn_workers)
    btn_payment = types.InlineKeyboardButton("💳 Способ оплаты", callback_data=f"payment_method_{shop_id}")
    btn_welcome = types.InlineKeyboardButton("👋 Приветствие", callback_data=f"edit_welcome_{shop_id}")
    markup.add(btn_payment, btn_welcome)
    markup.add(btn_delete)
    markup.add(btn_back)
    return markup

def create_workers_menu(shop_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Добавить работника", callback_data=f"add_worker_{shop_id}"))
    markup.add(types.InlineKeyboardButton("📋 Список работников", callback_data=f"list_workers_{shop_id}"))
    markup.add(types.InlineKeyboardButton("➖ Уволить работника", callback_data=f"remove_worker_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return markup

def create_remove_worker_menu(shop_id, workers):
    markup = types.InlineKeyboardMarkup()
    for worker_id, username in workers:
        worker_text = f"@{username}" if username else f"ID: {worker_id}"
        markup.add(types.InlineKeyboardButton(worker_text, callback_data=f"confirm_remove_{shop_id}_{worker_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"workers_{shop_id}"))
    return markup

def create_confirm_remove_menu(shop_id, worker_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("┼ТОЧНО?┼", callback_data=f"confirm_remove_step2_{shop_id}_{worker_id}"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"remove_worker_{shop_id}"))
    return markup

def create_confirm_remove_step2_menu(shop_id, worker_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("╤╧╨┼УВОЛИТЬ┼╨╧╤", callback_data=f"do_remove_{shop_id}_{worker_id}"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"remove_worker_{shop_id}"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_product_"))
def show_manager_product(call):
    parts = call.data.split("_")
    if len(parts) < 5:
        bot.answer_callback_query(call.id, "Неверные данные")
        return
    product_id = int(parts[2])
    category_id = int(parts[3])
    page = int(parts[4])
    product = get_product_info(product_id)
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден")
        return
    
    prod_id, cat_id, name, desc, price, image_path, created_at, popularity_score = product
    text = f"{name}\nЦена: {price}₽\nОписание: {desc or 'Нет'}"
    markup = create_edit_product_menu(product_id, category_id, page)
    
    if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
        with open(image_path, 'rb') as photo:
            bot.send_photo(call.message.chat.id, photo, caption=text, reply_markup=markup)
    else:
        bot.send_message(call.message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "back_from_desc")
def handle_back_from_desc(call):
    user_id = call.from_user.id
    user_states[user_id] = UserState.PRODUCT_PRICE
    bot.edit_message_text(
        "Введите цену товара (только положительное число):\n\nОтправьте 'назад' для отмены",
        call.message.chat.id,
        call.message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data == "default_image")
def handle_default_image(call):
    user_id = call.from_user.id
    category_id = user_states.get(f"{user_id}_category_id")
    product_name = user_states.get(f"{user_id}_product_name")
    product_price = user_states.get(f"{user_id}_product_price")
    description = user_states.get(f"{user_id}_product_description")
    
    # Добавляем товар со стандартным изображением
    image_path = "work_photos/default_not_image.jpg"
    product_id = add_product(category_id, product_name, product_price, image_path, description)
    
    if product_id:
        bot.answer_callback_query(call.id, "✅ Товар добавлен со стандартным изображением")
        bot.send_message(
            call.message.chat.id,
            "📦 Товары в разделе:",
            reply_markup=create_products_menu(category_id))
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при добавлении товара")
    
    # Очищаем состояние
    user_states[user_id] = UserState.SHOP_MENU
    for key in [f"{user_id}_category_id", f"{user_id}_product_name", 
                f"{user_id}_product_price", f"{user_id}_product_description"]:
        if key in user_states:
            del user_states[key]

@bot.callback_query_handler(func=lambda call: call.data == "skip_image")
def handle_skip_image(call):
    user_id = call.from_user.id
    category_id = user_states.get(f"{user_id}_category_id")
    product_name = user_states.get(f"{user_id}_product_name")
    product_price = user_states.get(f"{user_id}_product_price")
    description = user_states.get(f"{user_id}_product_description")
    
    # Добавляем товар без изображения
    product_id = add_product(category_id, product_name, product_price, None, description)
    
    if product_id:
        bot.answer_callback_query(call.id, "✅ Товар добавлен без изображения")
        bot.send_message(
            call.message.chat.id,
            "📦 Товары в разделе:",
            reply_markup=create_products_menu(category_id))
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при добавлении товара")
    
    # Очищаем состояние
    user_states[user_id] = UserState.SHOP_MENU
    for key in [f"{user_id}_category_id", f"{user_id}_product_name", 
                f"{user_id}_product_price", f"{user_id}_product_description"]:
        if key in user_states:
            del user_states[key]

@bot.callback_query_handler(func=lambda call: call.data == "back_from_image")
def handle_back_from_image(call):
    user_id = call.from_user.id
    user_states[user_id] = UserState.PRODUCT_DESCRIPTION
    bot.edit_message_text(
        "Введите описание товара (или '-' чтобы пропустить):",
        call.message.chat.id,
        call.message.message_id
    )

def create_categories_menu(shop_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    categories = get_shop_categories(shop_id)
    
    for category_id, category_name in categories:
        markup.add(types.InlineKeyboardButton(category_name, callback_data=f"category_{category_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Создать раздел", callback_data=f"create_category_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return markup

def create_category_actions_menu(category_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("📦 Просмотреть товары", callback_data=f"view_products_{category_id}"))
    markup.add(types.InlineKeyboardButton("✏️ Изменить название", callback_data=f"edit_category_name_{category_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ Удалить раздел", callback_data=f"delete_category_{category_id}"))
    shop_id = get_shop_id_by_category(category_id)
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_products_{shop_id}"))
    return markup

def create_products_menu(category_id, page=0):
    markup = types.InlineKeyboardMarkup(row_width=1)
    products = get_category_products(category_id)
    
    start_idx = page * 5
    end_idx = min(start_idx + 5, len(products))
    
    for i in range(start_idx, end_idx):
        product = products[i]
        product_id, name, price, image_path, description = product
        markup.add(types.InlineKeyboardButton(f"{name} - {price}₽", callback_data=f"product_{product_id}_{category_id}_{page}"))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️", callback_data=f"prev_page_{category_id}_{page-1}"))
    if end_idx < len(products):
        nav_buttons.append(types.InlineKeyboardButton("➡️", callback_data=f"next_page_{category_id}_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.add(types.InlineKeyboardButton("➕ Добавить товар", callback_data=f"add_product_{category_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"category_{category_id}"))
    return markup

# Функции для редактирования товаров
def create_edit_product_menu(product_id, category_id, page=0):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✏️ Изменить название", 
              callback_data=f"edit_name_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("💰 Изменить цену", 
              callback_data=f"edit_price_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("📝 Изменить описание", 
              callback_data=f"edit_desc_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🖼️ Изменить фото", 
              callback_data=f"edit_photo_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("👁️ Показать товар", 
              callback_data=f"show_product_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🗑️ Удалить товар", 
              callback_data=f"delete_product_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", 
              callback_data=f"back_to_products_{category_id}_{page}"))
    return markup

def create_back_button_menu(target_callback):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=target_callback))
    return markup

# Обработчики команд
@bot.message_handler(commands=['start'])
def start_handler(message):
    add_user(message.from_user.id, message.from_user.username)
    user_id = message.from_user.id
    user_states[user_id] = UserState.MAIN_MENU
    
    welcome_text = """🛍️ Добро пожаловать в Shop Manager Bot!

Этот бот поможет вам создать и управлять собственными магазинными ботами в Telegram.

Возможности:
• Создание неограниченного количества магазинов
• Управление товарами и категориями
• Настройка способов оплаты
• Просмотр отзывов и рейтингов

Выберите действие:"""
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=create_main_menu())

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.ADDING_WORKER)
def add_worker_handler(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    admin_input = message.text.strip()
    
    if admin_input.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        bot.send_message(
            message.chat.id,
            "❌ Добавление работника отменено",
            reply_markup=create_shop_management_menu(shop_id))
        return
    
    # Обработка username или ID
    admin_user_id = None
    username = None
    if admin_input.startswith('@'):
        username = admin_input[1:]
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Поиск или создание пользователя по username
        cursor.execute("SELECT tg_id FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        
        if result:
            admin_user_id = result[0]
        else:
            # Создаем временную запись пользователя
            cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
            admin_user_id = cursor.lastrowid
            conn.commit()
        conn.close()
    else:
        try:
            admin_user_id = int(admin_input)
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            
            # Поиск или создание пользователя по ID
            cursor.execute("SELECT tg_id, username FROM users WHERE tg_id = ?", (admin_user_id,))
            result = cursor.fetchone()
            if result:
                username = result[1]
            else:
                cursor.execute("INSERT INTO users (tg_id) VALUES (?)", (admin_user_id,))
                conn.commit()
        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите корректный @username или ID пользователя")
            return
        finally:
            conn.close()
    
    if not admin_user_id:
        bot.send_message(message.chat.id, "❌ Не удалось определить пользователя")
        return
    
    shop_info = get_shop_info(shop_id)
    if not shop_info:
        bot.send_message(message.chat.id, "❌ Магазин не найден")
        return
    
    # Проверка прав доступа
    if shop_info[1] != user_id:
        bot.send_message(message.chat.id, "❌ Только создатель магазина может добавлять работников")
        return
    
    if admin_user_id == shop_info[1]:
        bot.send_message(message.chat.id, "❌ Вы уже являетесь создателем этого магазина")
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Добавляем администратора (игнорируем дубликаты)
    cursor.execute("""
        INSERT OR IGNORE INTO shop_admins (shop_id, user_id) 
        VALUES (?, ?)
    """, (shop_id, admin_user_id))
    
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    if affected_rows > 0:
        # Уведомление добавленному работнику
        worker_message = f"🎉 Вы были добавлены как работник магазина '{shop_info[2]}'!\n\nТеперь вы можете управлять этим магазином через @{bot.get_me().username}"
        try:
            bot.send_message(admin_user_id, worker_message)
        except:
            pass
        
        # Уведомление всем админам (включая владельца)
        admins = get_shop_workers(shop_id)
        worker_display = f"@{username} (ID: {admin_user_id})" if username else f"User ID: {admin_user_id}"
        admin_message = f"Только что был добавлен новый сотрудник: {worker_display} в магазин '{shop_info[2]}'"
        for admin_id, _ in admins:
            try:
                bot.send_message(admin_id, admin_message)
            except:
                pass
        
        bot.send_message(
            message.chat.id,
            f"✅ Пользователь {admin_input} добавлен как работник",
            reply_markup=create_shop_management_menu(shop_id))
    else:
        bot.send_message(
            message.chat.id,
            f"ℹ️ Пользователь {admin_input} уже является работником",
            reply_markup=create_shop_management_menu(shop_id))
    
    user_states[user_id] = UserState.SHOP_MENU

@bot.message_handler(commands=['get_id'])
def get_user_id_handler(message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    
    response = f"📋 Ваша информация:\n"
    response += f"🆔 ID: {user_id}\n"
    response += f"👤 Имя: {first_name} {last_name}".strip()
    if username:
        response += f"\n📧 Username: @{username}"
    
    bot.send_message(message.chat.id, response)

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.EDITING_PAYMENT)
def save_payment_credentials(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    credentials = message.text.strip()
    
    if credentials.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        bot.send_message(
            message.chat.id,
            "❌ Настройка оплаты отменена",
            reply_markup=create_shop_management_menu(shop_id))
        return
    
    if ":" not in credentials:
        bot.send_message(message.chat.id, "❌ Формат неверный. Введите ShopID:SecretKey или 'назад' для отмены")
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE shops SET payment_method = 'online', yookassa_credentials = ? WHERE id = ?", (credentials, shop_id))
    conn.commit()
    conn.close()
    
    bot.send_message(
        message.chat.id,
        "✅ Настройки ЮKassa сохранены!",
        reply_markup=create_shop_management_menu(shop_id))
    user_states[user_id] = UserState.SHOP_MENU

# Обработчик callback-кнопок
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data
    try:
        if data == "main_menu":
            user_states[user_id] = UserState.MAIN_MENU
            bot.edit_message_text(
                "🏠 Главное меню\n\nВыберите действие:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_main_menu()
            )
        
        elif data == "reviews":
            bot.edit_message_text(
                "📊 Рейтинг магазинов\n\nМагазины отсортированы по рейтингу:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_reviews_menu()
            )
        
        elif data.startswith("reviews_page_"):
            page = int(data.split("_")[-1])
            bot.edit_message_text(
                "📊 Рейтинг магазинов\n\nМагазины отсортированы по рейтингу:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_reviews_menu(page)
            )
        
        elif data.startswith("shop_detail_"):
            shop_id = int(data.split("_")[-1])
            show_shop_detail(call, shop_id)
        
        elif data == "my_shops":
            bot.edit_message_text(
                "🏪 Ваши магазины:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_my_shops_menu(user_id)
            )
        
        elif data == "create_shop":
            user_states[user_id] = UserState.CREATING_SHOP
            bot.edit_message_text(
                "Введите название для нового магазина (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_back_button_menu("my_shops")
            )
        
        elif data.startswith("manage_shop_"):
            shop_id = int(data.split("_")[-1])
            show_shop_management(call, shop_id)
        
        elif data.startswith("edit_token_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.EDITING_TOKEN
            user_states[f"{user_id}_shop_id"] = shop_id
            
            shop_info = get_shop_info(shop_id)
            current_token = shop_info[3] if shop_info and shop_info[3] else "Не установлен"
            
            bot.edit_message_text(
                f"🔑 Токен API бота\n\nТекущий токен: {current_token}\n\nВведите новый токен (минимум 30 символов):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_back_button_menu(f"manage_shop_{shop_id}"))
        
        elif data == "botfather_instruction":
            instruction_text = """📖 Инструкция по получению токена от BotFather:

1️⃣ Найдите @BotFather в Telegram
2️⃣ Отправьте команду /newbot
3️⃣ Введите название вашего бота
4️⃣ Введите username бота (должен заканчиваться на 'bot')
5️⃣ Скопируйте полученный токен
6️⃣ Вернитесь и вставьте токен в настройки магазина

⚠️ Токен выглядит примерно так:
123456789:ABCdefGHIjklMNOpqrsTUVwxyz"""
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{user_states.get(f'{call.from_user.id}_shop_id', '')}"))
            
            bot.edit_message_text(
                instruction_text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
        
        elif data.startswith("manage_products_"):
            shop_id = int(data.split("_")[-1])
            bot.edit_message_text(
                "📦 Управление товарами\n\nВыберите раздел:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_categories_menu(shop_id))
        
        elif data.startswith("create_category_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.CREATING_CATEGORY
            user_states[f"{user_id}_shop_id"] = shop_id
            
            bot.edit_message_text(
                "Введите название для нового раздела (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_back_button_menu(f"manage_products_{shop_id}"))
        
        elif data.startswith("category_"):
            category_id = int(data.split("_")[-1])
            bot.edit_message_text(
                "📦 Действия с разделом:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_category_actions_menu(category_id))
        
        elif data.startswith("view_products_"):
            category_id = int(data.split("_")[-1])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_products_menu(category_id))
        
        elif data.startswith("show_product_"):
            parts = data.split("_")
            if len(parts) < 5:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            product_id = int(parts[2])
            category_id = int(parts[3])
            page = int(parts[4])
            show_manager_product(call, product_id, category_id, page)

        elif data.startswith("add_product_"):
            category_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.PRODUCT_NAME
            user_states[f"{user_id}_category_id"] = category_id
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"category_{category_id}"))
            
            bot.edit_message_text(
                "Введите название товара (минимум 2 символа):",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
        
        elif data.startswith("payment_method_"):
            shop_id = int(data.split("_")[-1])
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("Оплата на месте", callback_data=f"set_payment_cash_{shop_id}"),
                types.InlineKeyboardButton("Онлайн-оплата (ЮKassa)", callback_data=f"set_payment_online_{shop_id}"))
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
            bot.edit_message_text(
                "Выберите способ оплаты:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
        
        elif data.startswith("set_payment_"):
            shop_id = int(data.split("_")[-1])
            payment_type = "cash_on_delivery" if data.startswith("set_payment_cash_") else "online"
            user_states[user_id] = UserState.EDITING_PAYMENT if payment_type == "online" else UserState.SHOP_MENU
            
            if payment_type == "cash_on_delivery":
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE shops SET payment_method = ? WHERE id = ?", (payment_type, shop_id))
                conn.commit()
                conn.close()
                bot.edit_message_text(
                    "✅ Способ оплаты установлен: Оплата на месте",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_shop_management_menu(shop_id))
            else:
                bot.edit_message_text(
                    """Настройка ЮKassa:
1. Зарегистрируйтесь на https://yookassa.ru/
2. Получите Shop ID и Secret Key в личном кабинете
3. Введите данные в формате: ShopID:SecretKey
Пример: 123456:live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

Отправьте 'назад' для отмены""",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_back_button_menu(f"payment_method_{shop_id}"))
        
        elif data.startswith("edit_welcome_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.EDITING_WELCOME
            user_states[f"{user_id}_shop_id"] = shop_id
            
            bot.edit_message_text(
                "Введите новое приветственное сообщение для покупателей (минимум 5 символов):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_back_button_menu(f"manage_shop_{shop_id}"))
        
        elif data.startswith("delete_shop_"):
            shop_id = int(data.split("_")[-1])
            shop_info = get_shop_info(shop_id)
            if not shop_info:
                bot.answer_callback_query(call.id, "Магазин не найден")
                return
                
            if shop_info[1] != user_id:
                bot.answer_callback_query(call.id, "Только создатель магазина может его удалить")
                return
                
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM shops WHERE id = ?", (shop_id,))
            conn.commit()
            conn.close()
            if shop_id in active_shop_bots:
                try:
                    active_shop_bots[shop_id].stop_polling()
                except:
                    pass
                del active_shop_bots[shop_id]
            bot.edit_message_text(
                "✅ Магазин удалён",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_my_shops_menu(user_id))
        
        elif data == "get_user_id_info":
            bot.edit_message_text(
                "🆔 Как узнать ID пользователя:\n\n"
                "1️⃣ Попросите пользователя написать команду /get_id в этом боте\n"
                "2️⃣ Бот покажет его ID\n"
                "3️⃣ Используйте этот ID для добавления работника\n\n"
                "💡 ID выглядит как число, например: 123456789",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{user_states.get(f'{call.from_user.id}_shop_id', '')}"))
                )
        
        elif data.startswith("workers_"):
            shop_id = int(data.split("_")[1])
            bot.edit_message_text(
                "👥 Управление работниками магазина:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_workers_menu(shop_id))
        
        elif data.startswith("add_worker_"):
            shop_id = int(data.split("_")[2])
            user_states[user_id] = UserState.ADDING_WORKER
            user_states[f"{user_id}_shop_id"] = shop_id
            bot.edit_message_text(
                "👤 Добавление работника\n\n"
                "Введите @username или ID пользователя\n\n",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_back_button_menu(f"workers_{shop_id}"))
        
        elif data.startswith("list_workers_"):
            shop_id = int(data.split("_")[2])
            workers = get_shop_workers(shop_id)
            if not workers:
                bot.edit_message_text(
                    "В магазине нет работников",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_workers_menu(shop_id))
                return
            
            response = "👥 Список работников:\n\n"
            for worker_id, username in workers:
                response += f"• @{username} (ID: {worker_id})\n" if username else f"• ID: {worker_id}\n"
            
            bot.edit_message_text(
                response,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_workers_menu(shop_id))
        
        elif data.startswith("remove_worker_"):
            shop_id = int(data.split("_")[2])
            workers = get_shop_workers(shop_id)
            shop_info = get_shop_info(shop_id)
            owner_id = shop_info[1]
            workers = [(w[0], w[1]) for w in workers if w[0] != owner_id]
            
            if not workers:
                bot.answer_callback_query(call.id, "Нет работников для увольнения")
                return
            
            bot.edit_message_text(
                "Выберите работника для увольнения:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_remove_worker_menu(shop_id, workers))
        
        elif data.startswith("confirm_remove_"):
            parts = data.split("_")
            if len(parts) < 3:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            if "step2" in parts:
                shop_id = int(parts[3])
                worker_id = int(parts[4])
                bot.edit_message_text(
                    "А вдруг у него семья?😭",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_confirm_remove_step2_menu(shop_id, worker_id))
            else:
                shop_id = int(parts[2])
                worker_id = int(parts[3])
                bot.edit_message_text(
                    "Вы уверены, что хотите уволить этого работника (может не надо)?",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_confirm_remove_menu(shop_id, worker_id))
        
        elif data.startswith("do_remove_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            shop_id = int(parts[2])
            worker_id = int(parts[3])
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM users WHERE tg_id = ?", (worker_id,))
            result = cursor.fetchone()
            cursor.execute(f"""
                DELETE FROM shop_admins
                WHERE user_id = '{worker_id}' AND shop_id = '{shop_id}'
            """)
            username = result[0] if result else None
            conn.close()
            shop_info = get_shop_info(shop_id)
            if remove_worker(shop_id, worker_id):
                worker_message = f"Вы были удалены как работник из магазина '{shop_info[2]}'"
                try:
                    bot.send_message(worker_id, worker_message)
                except:
                    pass
                
                # Уведомление всем админам (включая владельца)
                admins = get_shop_workers(shop_id)
                worker_display = f"@{username} (ID: {worker_id})" if username else f"User ID: {worker_id}"
                admin_message = f"Был уволен работник: {worker_display} из магазина '{shop_info[2]}'"
                for admin_id, _ in admins:
                    try:
                        bot.send_message(admin_id, admin_message)
                    except:
                        pass
                bot.answer_callback_query(call.id, "✅ Работник был отправлен на рынок труда")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка при удалении")
            bot.edit_message_text(
                "👥 Управление работниками магазина:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_workers_menu(shop_id))
        
        # Обработчики для редактирования категорий
        elif data.startswith("edit_category_name_"):
            category_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.EDITING_CATEGORY_NAME
            user_states[f"{user_id}_category_id"] = category_id
            bot.edit_message_text(
                "Введите новое название для раздела (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_back_button_menu(f"category_{category_id}"))
        
        elif data.startswith("delete_category_"):
            category_id = int(data.split("_")[-1])
            if delete_category(category_id):
                shop_id = get_shop_id_by_category(category_id)
                bot.edit_message_text(
                    "✅ Раздел удалён",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_categories_menu(shop_id))
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка при удалении раздела")
        
        elif data.startswith("product_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            product_id = int(parts[1])
            category_id = int(parts[2])
            page = int(parts[3])
            user_states[user_id] = UserState.EDITING_PRODUCT
            bot.edit_message_text(
                "Выберите действие:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_edit_product_menu(product_id, category_id, page))
        
        elif data.startswith("prev_page_"):
            parts = data.split("_")
            category_id = int(parts[2])
            page = int(parts[3])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_products_menu(category_id, page))
        
        elif data.startswith("next_page_"):
            parts = data.split("_")
            category_id = int(parts[2])
            page = int(parts[3])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_products_menu(category_id, page))
        

        elif data.startswith("edit_name_") or data.startswith("edit_price_") or data.startswith("edit_desc_") or data.startswith("edit_photo_"):
            parts = data.split("_")
            if len(parts) < 5:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            edit_type = parts[1]
            product_id = int(parts[2])
            category_id = int(parts[3])
            page = int(parts[4])
            user_states[user_id] = UserState.EDITING_PRODUCT
            user_states[f"{user_id}_edit_type"] = edit_type
            user_states[f"{user_id}_product_id"] = product_id
            user_states[f"{user_id}_category_id"] = category_id
            user_states[f"{user_id}_page"] = page
            
            prompt = {
                "name": "Введите новое название (мин 2 символа):\n\nОтправьте 'назад' для отмены",
                "price": "Введите новую цену (положительное число):\n\nОтправьте 'назад' для отмены",
                "desc": "Введите новое описание:\n\nОтправьте 'назад' для отмены",
                "photo": "Отправьте новое фото или текст 'пропустить'/'стандартное':\n\nОтправьте 'назад' для отмены"
            }[edit_type]
            
            # Удаляем текущее сообщение и создаем новое
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            send_edit_prompt(
                call.message.chat.id,
                prompt,
                f"product_{product_id}_{category_id}_{page}"
            )
        
        elif data.startswith("delete_product_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            product_id = int(parts[2])
            category_id = int(parts[3])
            page = int(parts[4])
            delete_product(product_id)
            bot.answer_callback_query(call.id, "✅ Товар удалён")
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_products_menu(category_id, page))
        
        elif data.startswith("back_to_products_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            category_id = int(parts[3])
            page = int(parts[4])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_products_menu(category_id, page))
        
        elif data.startswith("all_products_"):
            shop_id = int(data.split("_")[-1])
            products = get_all_shop_products(shop_id)
            if not products:
                bot.edit_message_text(
                    "В магазине нет товаров.",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_back_button_menu(f"manage_shop_{shop_id}"))
                return
            
            text = "📦 Все товары в магазине:\n\n"
            for cat_name, name, price, desc in products:
                text += f"[{cat_name}] {name} - {price}₽"
                if desc:
                    text += f"\n   {desc}\n"
                else:
                    text += "\n"
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
            
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            
    except telebot.apihelper.ApiTelegramException as e:
        if e.error_code == 400 and 'message is not modified' in str(e):
            pass  # Ignore 'message not modified' error
        else:
            logging.error(f"Callback error: {str(e)}")
            bot.answer_callback_query(call.id, "Произошла ошибка. Попробуйте снова.")
    except Exception as e:
        logging.error(f"Callback error: {str(e)}")
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)[:100]}")

def show_shop_detail(call, shop_id):
    shop_info = get_shop_info(shop_id)
    if not shop_info:
        bot.answer_callback_query(call.id, "Магазин не найден")
        return
    
    shop_name = shop_info[2]
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT AVG(rating), COUNT(*) FROM reviews WHERE shop_id = ?", (shop_id,))
    result = cursor.fetchone()
    conn.close()
    
    avg_rating = float(result[0] or 0)
    review_count = result[1] or 0
    rating_stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
    
    detail_text = f"""🏪 {shop_name}

⭐ Рейтинг: {rating_stars} ({avg_rating:.1f}/5)
📊 Отзывов: {review_count}"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="reviews"))
    
    bot.edit_message_text(
        detail_text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

def show_shop_management(call, shop_id):
    shop_info = get_shop_info(shop_id)
    if not shop_info:
        bot.answer_callback_query(call.id, "Магазин не найден")
        return
    
    shop_name = shop_info[2]
    bot_token = shop_info[3] if shop_info[3] else "Не установлен"
    
    management_text = f"""⚙️ Управление магазином: {shop_name}

🔑 Токен API: {'Установлен' if bot_token != 'Не установлен' else 'Не установлен'}

Выберите действие для настройки:"""
    
    bot.edit_message_text(
        management_text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_shop_management_menu(shop_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("payment_method_"))
def payment_method_handler(call):
    shop_id = int(call.data.split("_")[-1])
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Оплата на месте", callback_data=f"set_payment_cash_{shop_id}"),
        types.InlineKeyboardButton("Онлайн-оплата (ЮKassa)", callback_data=f"set_payment_online_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    bot.edit_message_text(
        "Выберите способ оплаты:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_welcome_"))
def edit_welcome_handler(call):
    shop_id = int(call.data.split("_")[-1])
    user_states[call.from_user.id] = UserState.EDITING_WELCOME
    user_states[f"{call.from_user.id}_shop_id"] = shop_id
    
    bot.edit_message_text(
        "Введите новое приветственное сообщение для покупателей (минимум 5 символов):\n\nОтправьте 'назад' для отмены",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_back_button_menu(f"manage_shop_{shop_id}"))

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.EDITING_WELCOME)
def save_welcome_message(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    welcome_message = message.text.strip()
    
    if welcome_message.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        bot.send_message(
            message.chat.id,
            "❌ Изменение приветствия отменено",
            reply_markup=create_shop_management_menu(shop_id))
        return
    
    if len(welcome_message) < 5:
        bot.send_message(message.chat.id, "❌ Сообщение слишком короткое (мин. 5 символов). Попробуйте снова или отправьте 'назад' для отмены")
        return
    
    if update_welcome_message(shop_id, welcome_message):
        bot.send_message(
            message.chat.id,
            "✅ Приветственное сообщение обновлено!",
            reply_markup=create_shop_management_menu(shop_id))
        user_states[user_id] = UserState.SHOP_MENU
    else:
        bot.send_message(message.chat.id, "❌ Ошибка при обновлении сообщения")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_payment_"))
def set_payment_handler(call):
    shop_id = int(call.data.split("_")[-1])
    payment_type = "cash_on_delivery" if call.data.startswith("set_payment_cash_") else "online"
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE shops SET payment_method = ? WHERE id = ?", (payment_type, shop_id))
    conn.commit()
    conn.close()
    
    bot.edit_message_text(
        f"✅ Способ оплаты установлен: {'Оплата на месте' if payment_type == 'cash_on_delivery' else 'Онлайн-оплата'}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_shop_management_menu(shop_id))

# Обработчик текстовых сообщений
@bot.message_handler(func=lambda message: True)
def text_handler(message):
    user_id = message.from_user.id
    user_state = user_states.get(user_id)
    
    if user_state == UserState.CREATING_SHOP:
        shop_name = message.text.strip()
        
        if shop_name.lower() == 'назад':
            user_states[user_id] = UserState.MAIN_MENU
            bot.send_message(
                message.chat.id,
                "❌ Создание магазина отменено",
                reply_markup=create_main_menu())
            return
            
        if len(shop_name) < 2:
            bot.send_message(message.chat.id, "❌ Название магазина должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        shop_id = create_shop(user_id, shop_name)
        if not shop_id:
            bot.send_message(message.chat.id, "❌ Ошибка при создании магазина")
            return
            
        user_states[user_id] = UserState.SHOP_MENU
        
        bot.send_message(
            message.chat.id,
            f"✅ Магазин '{shop_name}' успешно создан!\n\nТеперь настройте его:",
            reply_markup=create_shop_management_menu(shop_id))
    
    elif user_state == UserState.EDITING_TOKEN:
        token = message.text.strip()
        shop_id = user_states.get(f"{user_id}_shop_id")
        
        if token.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            bot.send_message(
                message.chat.id,
                "❌ Изменение токена отменено",
                reply_markup=create_shop_management_menu(shop_id))
            return
            
        if len(token) < 30:
            bot.send_message(message.chat.id, "❌ Токен должен содержать не менее 30 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        update_shop_token(shop_id, token)
        user_states[user_id] = UserState.SHOP_MENU
        bot.send_message(
            message.chat.id,
            "✅ Токен успешно обновлен!",
            reply_markup=create_shop_management_menu(shop_id))
    
    elif user_state == UserState.CREATING_CATEGORY:
        category_name = message.text.strip()
        shop_id = user_states.get(f"{user_id}_shop_id")
        
        if category_name.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            bot.send_message(
                message.chat.id,
                "❌ Создание раздела отменено",
                reply_markup=create_shop_management_menu(shop_id))
            return
            
        if len(category_name) < 2:
            bot.send_message(message.chat.id, "❌ Название раздела должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        category_id = create_category(shop_id, category_name)
        if not category_id:
            bot.send_message(message.chat.id, "❌ Ошибка при создании раздела")
            return
            
        bot.send_message(
            message.chat.id,
            f"✅ Раздел '{category_name}' создан!",
            reply_markup=create_categories_menu(shop_id))
        user_states[user_id] = UserState.SHOP_MENU
    
    elif user_state == UserState.EDITING_CATEGORY_NAME:
        new_name = message.text.strip()
        category_id = user_states.get(f"{user_id}_category_id")
        shop_id = get_shop_id_by_category(category_id)
        
        if new_name.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            bot.send_message(
                message.chat.id,
                "❌ Изменение названия раздела отменено",
                reply_markup=create_categories_menu(shop_id))
            return
            
        if len(new_name) < 2:
            bot.send_message(message.chat.id, "❌ Название раздела должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        if update_category_name(category_id, new_name):
            bot.send_message(
                message.chat.id,
                f"✅ Название раздела изменено на '{new_name}'!",
                reply_markup=create_categories_menu(shop_id))
            user_states[user_id] = UserState.SHOP_MENU
        else:
            bot.send_message(message.chat.id, "❌ Ошибка при изменении названия раздела")
    
    elif user_state == UserState.PRODUCT_NAME:
        product_name = message.text.strip()
        if product_name.lower() == 'назад':
            category_id = user_states.get(f"{user_id}_category_id")
            bot.send_message(
                message.chat.id,
                "❌ Добавление товара отменено",
                reply_markup=create_products_menu(category_id))
            user_states[user_id] = UserState.SHOP_MENU
            return
            
        if len(product_name) < 2:
            bot.send_message(message.chat.id, "❌ Название товара должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        user_states[f"{user_id}_product_name"] = product_name
        user_states[user_id] = UserState.PRODUCT_PRICE
        
        bot.send_message(message.chat.id, "Введите цену товара (только положительное число):\n\nОтправьте 'назад' для отмены")
    
    elif user_state == UserState.PRODUCT_PRICE:
        price_text = message.text.strip()
        if price_text.lower() == 'назад':
            user_states[user_id] = UserState.PRODUCT_NAME
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"category_{user_states.get(f'{user_id}_category_id')}"))
            bot.send_message(
                message.chat.id,
                "❌ Ввод цены отменен\nВведите название товара:",
                reply_markup=markup
            )
            return
            
        try:
            price = float(price_text)
            if price <= 0:
                raise ValueError()
            user_states[f"{user_id}_product_price"] = price
            user_states[user_id] = UserState.PRODUCT_DESCRIPTION  # Переход к описанию
            
            # Создаем клавиатуру с кнопкой "Назад"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_from_desc"))
            
            bot.send_message(
                message.chat.id,
                "Введите описание товара (или '-' чтобы пропустить):",
                reply_markup=markup
            )
        except:
            bot.send_message(message.chat.id, "❌ Некорректная цена. Введите положительное число")
            return
        
    elif user_state == UserState.PRODUCT_DESCRIPTION:
        description = message.text.strip()
        if description == '-':
            description = None
            
        user_states[f"{user_id}_product_description"] = description
        user_states[user_id] = UserState.PRODUCT_IMAGE
        
        # Создаем клавиатуру с кнопками
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🖼️ Стандартное фото", callback_data="default_image"),
            types.InlineKeyboardButton("⏩ Пропустить", callback_data="skip_image"),
            types.InlineKeyboardButton("⬅️ Назад", callback_data="back_from_image")
        )
        
        bot.send_message(
            message.chat.id,
            "Отправьте изображение товара или выберите опцию:",
            reply_markup=markup
        )
    elif user_state == UserState.EDITING_PRODUCT:
        edit_type = user_states.get(f"{user_id}_edit_type")
        product_id = user_states.get(f"{user_id}_product_id")
        category_id = user_states.get(f"{user_id}_category_id")
        page = user_states.get(f"{user_id}_page", 0)
        
        if message.text.strip().lower() == 'назад':
            # Удаляем сообщение с запросом
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass
            
            bot.send_message(
                message.chat.id,
                "❌ Изменение товара отменено"
            )
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=create_products_menu(category_id, page))
            user_states[user_id] = UserState.SHOP_MENU
            return
        
        if edit_type == "name":
            new_name = message.text.strip()
            if len(new_name) < 2:
                bot.send_message(message.chat.id, "❌ Название товара должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
                return
            update_product(product_id, name=new_name)
            bot.send_message(
                message.chat.id,
                "✅ Название товара обновлено!"
            )
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=create_products_menu(category_id, page))
        
        elif edit_type == "price":
            try:
                new_price = float(message.text.strip())
                if new_price <= 0:
                    raise ValueError()
                update_product(product_id, price=new_price)
                bot.send_message(
                    message.chat.id,
                    "✅ Цена товара обновлена!"
                )
                bot.send_message(
                    message.chat.id,
                    "📦 Товары в разделе:",
                    reply_markup=create_products_menu(category_id, page))
            except:
                bot.send_message(message.chat.id, "❌ Некорректная цена. Введите положительное число или 'назад' для отмены")
                return
        
        elif edit_type == "desc":
            new_description = message.text.strip()
            update_product(product_id, description=new_description)
            bot.send_message(
                message.chat.id,
                "✅ Описание товара обновлено!"
            )
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=create_products_menu(category_id, page))
        
        elif edit_type == "photo":
            text = message.text.strip().lower()
            if text == 'пропустить':
                bot.send_message(
                    message.chat.id,
                    "✅ Изображение оставлено без изменений!"
                )
            elif text == 'стандартное':
                update_product(product_id, image_path="work_photos/default_not_image.jpg")
                bot.send_message(
                    message.chat.id,
                    "✅ Установлено стандартное изображение!"
                )
            else:
                bot.send_message(message.chat.id, "❌ Некорректная опция. Отправьте фото, 'Пропустить', 'Стандартное' или 'назад'")
                return
            
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=create_products_menu(category_id, page))
        
        user_states[user_id] = UserState.SHOP_MENU

@bot.callback_query_handler(func=lambda call: call.data.startswith("back_to_products_"))
def handle_back_to_products(call):
    parts = call.data.split("_")
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "Неверные данные")
        return
    category_id = int(parts[3])
    page = int(parts[4])
    
    bot.edit_message_text(
        "📦 Товары в разделе:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_products_menu(category_id, page))

@bot.message_handler(content_types=['photo'], 
                    func=lambda message: user_states.get(message.from_user.id) == UserState.PRODUCT_IMAGE)
def handle_product_image_photo(message):
    user_id = message.from_user.id
    if message.caption and message.caption.strip().lower() == 'назад':
        user_states[user_id] = UserState.PRODUCT_DESCRIPTION
        bot.send_message(
            message.chat.id,
            "❌ Добавление фото отменено\nВведите описание товара:"
        )
        return
        
    # СОЗДАЕМ ДИРЕКТОРИЮ, ЕСЛИ ЕЁ НЕТ
    if not os.path.exists("product_images"):
        os.makedirs("product_images")

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    # Генерация уникального имени файла
    image_path = f"product_images/{uuid.uuid4().hex}.jpg"
    with open(image_path, 'wb') as new_file:
        new_file.write(downloaded_file)
    
    category_id = user_states.get(f"{user_id}_category_id")
    product_name = user_states.get(f"{user_id}_product_name")
    product_price = user_states.get(f"{user_id}_product_price")
    description = user_states.get(f"{user_id}_product_description")
    
    product_id = add_product(category_id, product_name, product_price, image_path, description)
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка при добавлении товара")
        return
    
    bot.send_message(
        message.chat.id,
        f"✅ Товар '{product_name}' добавлен!"
    )
    bot.send_message(
        message.chat.id,
        "📦 Товары в разделе:",
        reply_markup=create_products_menu(category_id))
    
    # Очищаем состояние и временные данные
    user_states[user_id] = UserState.SHOP_MENU
    for key in [f"{user_id}_category_id", f"{user_id}_product_name", 
                f"{user_id}_product_price", f"{user_id}_product_description"]:
        if key in user_states:
            del user_states[key]

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.PRODUCT_IMAGE,
                    content_types=['text'])
def handle_product_image_text(message):
    user_id = message.from_user.id
    text = message.text.strip().lower()
    category_id = user_states.get(f"{user_id}_category_id")
    product_name = user_states.get(f"{user_id}_product_name")
    product_price = user_states.get(f"{user_id}_product_price")
    description = user_states.get(f"{user_id}_product_description")
    
    if text == 'назад':
        user_states[user_id] = UserState.PRODUCT_DESCRIPTION
        bot.send_message(
            message.chat.id,
            "❌ Добавление изображения отменено\nВведите описание товара:"
        )
        return
        
    image_path = None
    if text == 'пропустить':
        image_path = None
    elif text == 'стандартное':
        # Исправлено: правильный путь к стандартному изображению
        image_path = "work_photos/default_not_image.jpg"
    else:
        bot.send_message(message.chat.id, "❌ Некорректная опция. Отправьте фото, 'Пропустить', 'Стандартное' или 'назад'")
        return
        
    product_id = add_product(category_id, product_name, product_price, image_path, description)
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка при добавлении товара")
        return
        
    bot.send_message(
        message.chat.id,
        f"✅ Товар '{product_name}' добавлен!"
    )
    bot.send_message(
        message.chat.id,
        "📦 Товары в разделе:",
        reply_markup=create_products_menu(category_id))
    
    # Очищаем состояние и временные данные
    user_states[user_id] = UserState.SHOP_MENU
    for key in [f"{user_id}_category_id", f"{user_id}_product_name", 
                f"{user_id}_product_price", f"{user_id}_product_description"]:
        if key in user_states:
            del user_states[key]

        elif user_states == UserState.EDITING_PRODUCT:
            edit_type = user_states.get(f"{user_id}_edit_type")
            product_id = user_states.get(f"{user_id}_product_id")
            category_id = user_states.get(f"{user_id}_category_id")
            page = user_states.get(f"{user_id}_page", 0)
            
            if message.text.strip().lower() == 'назад':
                bot.send_message(
                    message.chat.id,
                    "❌ Изменение товара отменено"
                )
                bot.send_message(
                    message.chat.id,
                    "📦 Товары в разделе:",
                    reply_markup=create_products_menu(category_id, page))
                user_states[user_id] = UserState.SHOP_MENU
                return
            
            if edit_type == "name":
                new_name = message.text.strip()
                if len(new_name) < 2:
                    bot.send_message(message.chat.id, "❌ Название товара должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
                    return
                update_product(product_id, name=new_name)
                bot.send_message(
                    message.chat.id,
                    "✅ Название товара обновлено!"
                )
                bot.send_message(
                    message.chat.id,
                    "📦 Товары в разделе:",
                    reply_markup=create_products_menu(category_id, page))
            
            elif edit_type == "price":
                try:
                    new_price = float(message.text.strip())
                    if new_price <= 0:
                        raise ValueError()
                    update_product(product_id, price=new_price)
                    bot.send_message(
                        message.chat.id,
                        "✅ Цена товара обновлена!"
                    )
                    bot.send_message(
                        message.chat.id,
                        "📦 Товары в разделе:",
                        reply_markup=create_products_menu(category_id, page))
                except:
                    bot.send_message(message.chat.id, "❌ Некорректная цена. Введите положительное число или 'назад' для отмены")
                    return
            
            elif edit_type == "desc":
                new_description = message.text.strip()
                update_product(product_id, description=new_description)
                bot.send_message(
                    message.chat.id,
                    "✅ Описание товара обновлено!"
                )
                bot.send_message(
                    message.chat.id,
                    "📦 Товары в разделе:",
                    reply_markup=create_products_menu(category_id, page))
            
            elif edit_type == "photo":
                text = message.text.strip().lower()
                if text == 'пропустить':
                    # Не обновляем изображение
                    bot.send_message(
                        message.chat.id,
                        "✅ Изображение оставлено без изменений!"
                    )
                elif text == 'стандартное':
                    update_product(product_id, image_path="default_not_image.jpg")
                    bot.send_message(
                        message.chat.id,
                        "✅ Установлено стандартное изображение!"
                    )
                else:
                    bot.send_message(message.chat.id, "❌ Некорректная опция. Отправьте фото, 'Пропустить', 'Стандартное' или 'назад'")
                    return
                
                bot.send_message(
                    message.chat.id,
                    "📦 Товары в разделе:",
                    reply_markup=create_products_menu(category_id, page))
            
            user_states[user_id] = UserState.SHOP_MENU
    
        else:
            bot.send_message(
                message.chat.id,
                "Используйте кнопки меню для навигации:",
                reply_markup=create_main_menu())

@bot.message_handler(content_types=['photo'], 
                    func=lambda message: user_states.get(message.from_user.id) == UserState.EDITING_PRODUCT and 
                                       user_states.get(f"{message.from_user.id}_edit_type") == 'photo')
def handle_edit_product_photo(message):
    user_id = message.from_user.id
    product_id = user_states.get(f"{user_id}_product_id")
    category_id = user_states.get(f"{user_id}_category_id")
    page = user_states.get(f"{user_id}_page", 0)
    
    # Получаем информацию о товаре
    product = get_product_info(product_id)
    if not product:
        bot.send_message(message.chat.id, "❌ Товар не найден")
        return
    
    # Сохраняем путь к старому изображению
    old_image_path = product[5]
    
    # Создаем директорию для изображений, если её нет
    if not os.path.exists("product_images"):
        os.makedirs("product_images")

    # Скачиваем новое изображение
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    # Генерируем уникальное имя файла
    new_image_path = f"product_images/{uuid.uuid4().hex}.jpg"
    with open(new_image_path, 'wb') as new_file:
        new_file.write(downloaded_file)
    
    # Обновляем товар с новым путем к изображению
    update_product(product_id, image_path=new_image_path)
    
    # Удаляем старое изображение (если оно не стандартное)
    if old_image_path and os.path.exists(old_image_path) and "default_not_image" not in old_image_path:
        try:
            os.remove(old_image_path)
        except Exception as e:
            logging.error(f"Ошибка при удалении старого изображения: {e}")

    # Удаляем сообщение с фото
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass

    # Отправляем подтверждение
    bot.send_message(
        message.chat.id,
        "✅ Фото товара обновлено!"
    )
    bot.send_message(
        message.chat.id,
        "📦 Товары в разделе:",
        reply_markup=create_products_menu(category_id, page)
    )
    user_states[user_id] = UserState.SHOP_MENU


# Функция для запуска магазинного бота
def run_shop_bot(shop_id, bot_token, welcome_message):
    shop_bot = telebot.TeleBot(bot_token)
    active_shop_bots[shop_id] = shop_bot
    shop_bot_states = {}

    def create_shop_main_menu():
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_products = types.InlineKeyboardButton("📦 Товары", callback_data="shop_products")
        btn_cart = types.InlineKeyboardButton("🛒 Корзина", callback_data="view_cart")
        btn_reviews = types.InlineKeyboardButton("📊 Отзывы", callback_data="shop_reviews")
        btn_search = types.InlineKeyboardButton("🔍 Поиск", callback_data="shop_search")
        btn_recs = types.InlineKeyboardButton("✨ Похожие магазины", callback_data="shop_recommendations")
        markup.add(btn_products, btn_cart)
        markup.add(btn_reviews, btn_search)
        markup.add(btn_recs)
        return markup

    def show_products_list(call, products, title="Товары", back_data="shop_main_menu"):
        if not products:
            shop_bot.edit_message_text("Нет товаров.", call.message.chat.id, call.message.message_id, reply_markup=create_shop_main_menu())
            return
        text = title + "\n\n"
        markup = types.InlineKeyboardMarkup(row_width=1)
        for product in products:
            product_id = product[0]
            name = product[2] if len(product) > 5 else product[1]
            price = product[4] if len(product) > 5 else product[2]
            text += f"{name} (ID: {product_id}) - {price}₽\n"
            markup.add(types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=back_data))
        shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    # Ключевое исправление: корректная обработка данных товара
    def show_product_detail(call, product_id):
        product = get_product_info(product_id)
        if not product:
            shop_bot.answer_callback_query(call.id, "Товар не найден")
            return
        
        # Корректная распаковка всех данных товара
        prod_id = product[0]
        name = product[2]
        description = product[3]
        price = product[4]
        image_path = product[5]
        
        caption = f"📱 Артикул: {prod_id}\n📦 {name}\n💰 {price} RUB\n📝 {description or 'Нет описания'}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add_to_cart_{prod_id}"))
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_list"))
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as photo:
                shop_bot.send_photo(call.message.chat.id, photo, caption=caption, reply_markup=markup)
        else:
            shop_bot.send_message(call.message.chat.id, caption, reply_markup=markup)

    def create_filter_menu(user_id):
        filters = shop_bot_states.get(f"{user_id}_filters", {'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'})
        text = "Фильтры:\n"
        text += f"Мин. цена: {filters['price_min'] or 'не указано'}\n"
        text += f"Макс. цена: {filters['price_max'] or 'не указано'}\n"
        text += f"Категория: {get_category_name(filters['category_id']) if filters['category_id'] else 'все'}\n"
        text += f"Сортировка: {filters['sort_by']}\n"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Установить мин. цену", callback_data="set_min_price"))
        markup.add(types.InlineKeyboardButton("Установить макс. цену", callback_data="set_max_price"))
        markup.add(types.InlineKeyboardButton("Выбрать категорию", callback_data="choose_category"))
        markup.add(types.InlineKeyboardButton("Сорт. по цене ↑", callback_data="filter_sort_price_asc"))
        markup.add(types.InlineKeyboardButton("Сорт. по цене ↓", callback_data="filter_sort_price_desc"))
        markup.add(types.InlineKeyboardButton("Сорт. по популярности", callback_data="filter_sort_popularity"))
        markup.add(types.InlineKeyboardButton("Сорт. по новизне", callback_data="filter_sort_newest"))
        markup.add(types.InlineKeyboardButton("Применить", callback_data="apply_filters"))
        markup.add(types.InlineKeyboardButton("Сбросить", callback_data="reset_filters"))
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_search"))
        return text, markup

    def get_category_name(category_id):
        if not category_id:
            return None
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
        name = cursor.fetchone()
        conn.close()
        return name[0] if name else None

    def handle_delivery_address(message, product_id, shop_id, customer_id, shop_bot_states=None):
        delivery_address = message.text.strip()
        if not delivery_address:
            shop_bot.send_message(message.chat.id, "❌ Адрес не может быть пустым")
            return
            
        shop_info = get_shop_info(shop_id)
        if not shop_info:
            return
            
        payment_method = shop_info[4]
        
        items = get_cart_items(shop_id, customer_id)
        if not items:
            shop_bot.send_message(message.chat.id, "🛒 Ваша корзина пуста")
            return
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        order_ids = []
        total_price = 0
        order_details = ""
        
        for product_id, name, price, quantity in items:
            total_price += price * quantity
            order_details += f"📦 {name} x{quantity} - {price * quantity}₽\n"
            cursor.execute("INSERT INTO orders (shop_id, customer_user_id, product_id, delivery_address) VALUES (?, ?, ?, ?)",
                         (shop_id, customer_id, product_id, delivery_address))
            order_ids.append(cursor.lastrowid)
        
        conn.commit()
        conn.close()
        
        admin_ids = [shop_info[1]]
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shop_admins WHERE shop_id = ?", (shop_id,))
        admin_ids.extend([row[0] for row in cursor.fetchall()])
        conn.close()
        
        for admin_id in admin_ids:
            try:
                bot.send_message(
                    admin_id,
                    f"🆕 Новый заказ!\n\nМагазин: {shop_info[2]}\n{order_details}💰 Итог: {total_price}₽\n🏠 Адрес: {delivery_address}\n👤 Покупатель: @{message.from_user.username or 'Не указан'}\n💳 Способ оплаты: {payment_method}"
                )
            except:
                pass
        
        if payment_method == 'online':
            payment_url = create_payment_link(total_price, order_ids[0], shop_id)
            if payment_url:
                shop_bot.send_message(
                    message.chat.id,
                    f"🛒 Заказ оформлен!\n\n{order_details}💰 Итог: {total_price}₽\n🏠 Адрес: {delivery_address}\n\nОплатите заказ по ссылке:\n{payment_url}"
                )
            else:
                shop_bot.send_message(
                    message.chat.id,
                    f"🛒 Заказ оформлен!\n\n{order_details}💰 Итог: {total_price}₽\n🏠 Адрес: {delivery_address}\n\n❌ Ошибка при создании платежной ссылки"
                )
        else:
            shop_bot.send_message(
                message.chat.id,
                f"🛒 Заказ оформлен!\n\n{order_details}💰 Итог: {total_price}₽\n🏠 Адрес: {delivery_address}\n💳 Способ оплаты: Оплата при получении"
            )
        
        clear_cart(shop_id, customer_id)
        if shop_bot_states:
            shop_bot_states[customer_id] = ShopBotState.MAIN_MENU

    @shop_bot.message_handler(commands=['start'])
    def shop_start_handler(message):
        add_user(message.from_user.id, message.from_user.username)
        user_id = message.from_user.id
        shop_bot_states[user_id] = ShopBotState.MAIN_MENU
        shop_bot.send_message(
            message.chat.id,
            welcome_message,
            reply_markup=create_shop_main_menu()
        )

    @shop_bot.callback_query_handler(func=lambda call: True)
    def shop_callback_handler(call):
        user_id = call.from_user.id
        data = call.data
        try:
            if data == "view_cart":
                shop_bot_states[user_id] = ShopBotState.VIEWING_CART
                items = get_cart_items(shop_id, user_id)
                if not items:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return

                total_price = 0
                cart_text = "🛒 Ваша корзина:\n\n"
                markup = types.InlineKeyboardMarkup(row_width=2)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name} x{quantity} - {item_total}₽\n"
                    markup.add(types.InlineKeyboardButton(f"❌ Удалить {name}", callback_data=f"remove_from_cart_{product_id}"))

                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.add(
                    types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    cart_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data.startswith("add_to_cart_"):
                product_id = int(data.split("_")[-1])
                if add_to_cart(shop_id, user_id, product_id):
                    shop_bot.answer_callback_query(call.id, "Товар добавлен в корзину!")
                else:
                    shop_bot.answer_callback_query(call.id, "❌ Ошибка при добавлении в корзину")

            elif data.startswith("remove_from_cart_"):
                product_id = int(data.split("_")[-1])
                remove_from_cart(shop_id, user_id, product_id)
                shop_bot.answer_callback_query(call.id, "Товар удален из корзины")
                items = get_cart_items(shop_id, user_id)
                if not items:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return

                total_price = 0
                cart_text = "🛒 Ваша корзина:\n\n"
                markup = types.InlineKeyboardMarkup(row_width=2)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name} x{quantity} - {item_total}₽\n"
                    markup.add(types.InlineKeyboardButton(f"❌ Удалить {name}", callback_data=f"remove_from_cart_{product_id}"))

                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.add(
                    types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    cart_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data == "clear_cart":
                clear_cart(shop_id, user_id)
                shop_bot.edit_message_text(
                    "🛒 Корзина очищена",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            elif data == "order_cart":
                items = get_cart_items(shop_id, user_id)
                if not items:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                shop_bot_states[user_id] = ShopBotState.ENTERING_ADDRESS
                shop_bot.edit_message_text(
                    "Введите адрес доставки:\n\nОтправьте 'назад' для отмены",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_back_button_menu("view_cart")
                )

            elif data == "shop_products":
                categories = get_shop_categories(shop_id)
                if not categories:
                    shop_bot.edit_message_text("В магазине нет товаров.", call.message.chat.id, call.message.message_id, reply_markup=create_shop_main_menu())
                    return
                if len(categories) == 1:
                    products = get_category_products(categories[0][0])
                    shop_bot_states[f"{user_id}_current_list"] = products
                    shop_bot_states[f"{user_id}_current_title"] = "Товары"
                    shop_bot_states[f"{user_id}_current_back"] = "shop_main_menu"
                    show_products_list(call, products, "Товары", "shop_main_menu")
                else:
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    for cat_id, name in categories:
                        markup.add(types.InlineKeyboardButton(name, callback_data=f"shop_category_{cat_id}"))
                    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                    shop_bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "shop_main_menu":
                shop_bot_states[user_id] = ShopBotState.MAIN_MENU
                shop_info = get_shop_info(shop_id)
                shop_bot.edit_message_text(
                    shop_info[5],
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            elif data == "shop_reviews":
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT AVG(rating), COUNT(*) FROM reviews WHERE shop_id = ?", (shop_id,))
                result = cursor.fetchone()
                avg_rating = float(result[0] or 0)
                review_count = result[1] or 0

                cursor.execute("""
                    SELECT u.username, r.rating, r.review_text
                    FROM reviews r
                    LEFT JOIN users u ON r.user_id = u.tg_id
                    WHERE r.shop_id = ?
                    ORDER BY r.created_at DESC
                    LIMIT 5
                """, (shop_id,))
                reviews = cursor.fetchall()
                conn.close()

                rating_stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"

                review_text = f"📊 Отзывы о магазине\n\n⭐ Средний рейтинг: {rating_stars} ({avg_rating:.1f}/5)\n📝 Количество отзывов: {review_count}\n\nПоследние отзывы:\n"
                if reviews:
                    for username, rating, text in reviews:
                        username = f"@{username}" if username else "Аноним"
                        stars = "⭐" * rating
                        review_content = text if text else "Без комментария"
                        review_text += f"{username}: {stars}\n{review_content}\n\n"
                else:
                    review_text += "Отзывов пока нет."

                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("💬 Оставить отзыв", callback_data="shop_leave_review"))
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    review_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data == "shop_leave_review":
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM reviews WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
                if cursor.fetchone():
                    shop_bot.answer_callback_query(call.id, "Вы уже оставили отзыв для этого магазина")
                    conn.close()
                    return
                conn.close()

                shop_bot_states[user_id] = ShopBotState.REVIEW_RATING
                markup = types.InlineKeyboardMarkup(row_width=5)
                for i in range(1, 6):
                    markup.add(types.InlineKeyboardButton(f"{i}⭐", callback_data=f"shop_rating_{i}"))
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_reviews"))
                shop_bot.edit_message_text(
                    "Оцените магазин от 1 до 5 звёзд:",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data.startswith("shop_rating_"):
                rating = int(data.split("_")[-1])
                shop_bot_states[user_id] = ShopBotState.REVIEW_TEXT
                shop_bot_states[f"{user_id}_rating"] = rating

                shop_bot.edit_message_text(
                    f"Вы поставили оценку: {rating}⭐\n\nТеперь напишите отзыв (или /skip чтобы пропустить):\n\nОтправьте 'назад' для отмены",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_back_button_menu("shop_leave_review")
                )

            elif data.startswith("shop_category_"):
                category_id = int(data.split("_")[-1])
                products = get_category_products(category_id)
                shop_bot_states[f"{user_id}_current_list"] = products
                shop_bot_states[f"{user_id}_current_title"] = "Товары в категории"
                shop_bot_states[f"{user_id}_current_back"] = "shop_products"
                show_products_list(call, products, "Товары в категории", "shop_products")

            elif data.startswith("view_product_"):
                product_id = int(data.split("_")[-1])
                show_product_detail(call, product_id)

            elif data == "back_to_list":
                # Удаляем сообщение с деталями товара
                try:
                    shop_bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass

                # Получаем сохраненные данные для показа списка
                products = shop_bot_states.get(f"{user_id}_current_list", [])
                title = shop_bot_states.get(f"{user_id}_current_title", "Товары")
                back_data = shop_bot_states.get(f"{user_id}_current_back", "shop_main_menu")

                if not products:
                    shop_bot.send_message(
                        call.message.chat.id,
                        "Нет товаров.",
                        reply_markup=create_shop_main_menu()
                    )
                    return

                text = title + "\n\n"
                markup = types.InlineKeyboardMarkup(row_width=1)
                for product in products:
                    product_id = product[0]
                    name = product[2] if len(product) > 5 else product[1]
                    price = product[4] if len(product) > 5 else product[2]
                    text += f"{name} (ID: {product_id}) - {price}₽\n"
                    markup.add(types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=back_data))

                shop_bot.send_message(
                    call.message.chat.id,
                    text,
                    reply_markup=markup
                )
        
            elif data == "shop_search":
                shop_bot_states[user_id] = ShopBotState.SEARCH_MODE
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton("По имени", callback_data="search_type_name"))
                markup.add(types.InlineKeyboardButton("По артикулу", callback_data="search_type_id"))
                markup.add(types.InlineKeyboardButton("Фильтры", callback_data="search_filters"))
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text("🔍 Поиск товаров:\nВыберите тип:", call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data.startswith("search_type_"):
                type_ = data.split("_")[-1]
                shop_bot_states[f"{user_id}_search_type"] = type_
                shop_bot_states[user_id] = ShopBotState.SEARCH_INPUT
                bot_text = "Введите артикул:" if type_ == 'id' else "Введите имя:"
                shop_bot.edit_message_text(bot_text, call.message.chat.id, call.message.message_id, reply_markup=create_back_button_menu("shop_search"))

            elif data == "search_filters":
                shop_bot_states[user_id] = ShopBotState.FILTER_MODE
                if f"{user_id}_filters" not in shop_bot_states:
                    shop_bot_states[f"{user_id}_filters"] = {'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'}
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "set_min_price":
                shop_bot_states[user_id] = ShopBotState.FILTER_MIN_PRICE
                shop_bot.edit_message_text("Введите минимальную цену:", call.message.chat.id, call.message.message_id, reply_markup=create_back_button_menu("search_filters"))

            elif data == "set_max_price":
                shop_bot_states[user_id] = ShopBotState.FILTER_MAX_PRICE
                shop_bot.edit_message_text("Введите максимальную цену:", call.message.chat.id, call.message.message_id, reply_markup=create_back_button_menu("search_filters"))

            elif data == "choose_category":
                categories = get_shop_categories(shop_id)
                markup = types.InlineKeyboardMarkup(row_width=2)
                for cat_id, name in categories:
                    markup.add(types.InlineKeyboardButton(name, callback_data=f"filter_category_{cat_id}"))
                markup.add(types.InlineKeyboardButton("Все категории", callback_data="filter_category_none"))
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="search_filters"))
                shop_bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data.startswith("filter_category_"):
                cat_id = data.split("_")[-1]
                filters = shop_bot_states[f"{user_id}_filters"]
                filters['category_id'] = None if cat_id == 'none' else int(cat_id)
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data.startswith("filter_sort_"):
                sort_type = data.split("_")[-1]
                filters = shop_bot_states[f"{user_id}_filters"]
                filters['sort_by'] = f"price_{sort_type}" if 'price' in sort_type else sort_type
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "reset_filters":
                shop_bot_states[f"{user_id}_filters"] = {'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'}
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "apply_filters":
                filters = shop_bot_states[f"{user_id}_filters"]
                results = search_products(shop_id, None, 'name', filters['price_min'], filters['price_max'], filters['category_id'], filters['sort_by'])
                shop_bot_states[f"{user_id}_current_list"] = results
                shop_bot_states[f"{user_id}_current_title"] = "Результаты поиска"
                shop_bot_states[f"{user_id}_current_back"] = "search_filters"
                show_products_list(call, results, "Результаты поиска", "search_filters")

            elif data == "shop_recommendations":
                shops = get_similar_shops(shop_id)
                text = "✨ Похожие магазины:\n\n"
                markup = types.InlineKeyboardMarkup()
                for s_id, name, username, rating in shops:
                    stars = "⭐" * int(rating)
                    text += f"{name} {stars} ({rating:.1f})\n"
                    if username:
                        markup.add(types.InlineKeyboardButton("Посетить магазин", url=f"https://t.me/{username}"))
                markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 400 and 'message is not modified' in str(e):
                pass
            else:
                logging.error(f"Shop callback error: {str(e)}")
                shop_bot.answer_callback_query(call.id, "Произошла ошибка. Попробуйте снова.")
        except Exception as e:
            logging.error(f"Shop callback error: {str(e)}")
            shop_bot.answer_callback_query(call.id, f"Ошибка: {str(e)[:100]}")

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.ENTERING_ADDRESS)
    def handle_cart_delivery_address(message):
        if message.text.strip().lower() == 'назад':
            shop_bot_states[message.from_user.id] = ShopBotState.VIEWING_CART
            shop_bot.send_message(
                message.chat.id,
                "❌ Оформление заказа отменено",
                reply_markup=create_back_button_menu("view_cart")
            )
            return
        handle_delivery_address(message, None, shop_id, message.from_user.id, shop_bot_states)

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.REVIEW_TEXT)
    def handle_review_text(message):
        user_id = message.from_user.id
        if message.text.strip().lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.REVIEW_RATING
            markup = types.InlineKeyboardMarkup(row_width=5)
            for i in range(1, 6):
                markup.add(types.InlineKeyboardButton(f"{i}⭐", callback_data=f"shop_rating_{i}"))
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_reviews"))
            shop_bot.send_message(
                message.chat.id,
                "Оцените магазин от 1 до 5 звёзд:",
                reply_markup=markup
            )
            return
            
        rating = shop_bot_states.get(f"{user_id}_rating")
        if message.text.strip() == "/skip":
            review_text = ""
        else:
            review_text = message.text.strip()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reviews (shop_id, user_id, rating, review_text) VALUES (?, ?, ?, ?)",
                      (shop_id, user_id, rating, review_text))
        username = message.from_user.username or None
        cursor.execute("INSERT OR IGNORE INTO users (tg_id, username) VALUES (?, ?)",
                      (user_id, username))
        cursor.execute("UPDATE users SET username = ? WHERE tg_id = ?",
                      (username, user_id))
        conn.commit()
        conn.close()
        
        shop_bot.send_message(
            message.chat.id,
            "✅ Спасибо за ваш отзыв!",
            reply_markup=create_shop_main_menu())
        shop_bot_states[user_id] = ShopBotState.MAIN_MENU

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.SEARCH_INPUT)
    def handle_search_input(message):
        query = message.text.strip()
        if query.lower() == 'назад':
            shop_bot_states[message.from_user.id] = ShopBotState.SEARCH_MODE
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("По имени", callback_data="search_type_name"))
            markup.add(types.InlineKeyboardButton("По артикулу", callback_data="search_type_id"))
            markup.add(types.InlineKeyboardButton("Фильтры", callback_data="search_filters"))
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
            shop_bot.send_message(message.chat.id, "🔍 Поиск товаров:\nВыберите тип:", reply_markup=markup)
            return
        type_ = shop_bot_states.get(f"{message.from_user.id}_search_type")
        if type_ == 'id' and not query.isdigit():
            shop_bot.send_message(message.chat.id, "Артикул должен быть числом. Попробуйте снова.")
            return
        results = search_products(shop_id, query, type_)
        shop_bot_states[f"{message.from_user.id}_current_list"] = results
        shop_bot_states[f"{message.from_user.id}_current_title"] = "Результаты поиска"
        shop_bot_states[f"{message.from_user.id}_current_back"] = "shop_search"
        text = "Результаты поиска:\n\n"
        markup = types.InlineKeyboardMarkup(row_width=1)
        for product in results:
            product_id, _, name, _, price, _ = product[:6]
            text += f"{name} (ID: {product_id}) - {price}₽\n"
            markup.add(types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_search"))
        shop_bot.send_message(message.chat.id, text, reply_markup=markup)

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.FILTER_MIN_PRICE)
    def handle_filter_min_price(message):
        text = message.text.strip()
        if text.lower() == 'назад':
            shop_bot_states[message.from_user.id] = ShopBotState.FILTER_MODE
            text, markup = create_filter_menu(message.from_user.id)
            shop_bot.send_message(message.chat.id, text, reply_markup=markup)
            return
        try:
            value = float(text)
            if value < 0:
                raise ValueError
            filters = shop_bot_states[f"{message.from_user.id}_filters"]
            filters['price_min'] = value
            shop_bot_states[message.from_user.id] = ShopBotState.FILTER_MODE
            text, markup = create_filter_menu(message.from_user.id)
            shop_bot.send_message(message.chat.id, text, reply_markup=markup)
        except:
            shop_bot.send_message(message.chat.id, "Некорректная цена. Введите положительное число или 'назад'.")

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.FILTER_MAX_PRICE)
    def handle_filter_max_price(message):
        text = message.text.strip()
        if text.lower() == 'назад':
            shop_bot_states[message.from_user.id] = ShopBotState.FILTER_MODE
            text, markup = create_filter_menu(message.from_user.id)
            shop_bot.send_message(message.chat.id, text, reply_markup=markup)
            return
        try:
            value = float(text)
            if value < 0:
                raise ValueError
            filters = shop_bot_states[f"{message.from_user.id}_filters"]
            filters['price_max'] = value
            shop_bot_states[message.from_user.id] = ShopBotState.FILTER_MODE
            text, markup = create_filter_menu(message.from_user.id)
            shop_bot.send_message(message.chat.id, text, reply_markup=markup)
        except:
            shop_bot.send_message(message.chat.id, "Некорректная цена. Введите положительное число или 'назад'.")

    try:
        shop_bot.infinity_polling()
    except Exception as e:
        logging.error(f"Ошибка в боте магазина {shop_id}: {e}")


if __name__ == "__main__":
    print("Инициализация базы данных...")
    init_database()
    print("База данных готова!")
    
    print(f"Бот-менеджер запущен! Токен: {BOT_TOKEN}")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, bot_token, welcome_message FROM shops WHERE bot_token IS NOT NULL AND is_running = 1")
    shops = cursor.fetchall()
    conn.close()
    
    for shop_id, bot_token, welcome_message in shops:
        threading.Thread(target=run_shop_bot, args=(shop_id, bot_token, welcome_message), daemon=True).start()
    
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")