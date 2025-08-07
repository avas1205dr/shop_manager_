import telebot
import sqlite3
from telebot import types

# Вставьте сюда ваш токен бота
TOKEN = '7793591374:AAHYhGqYiNgg3EqKvSJFHsFxGCgpEKw7mgk'

bot = telebot.TeleBot(TOKEN)

# Подключение к базе данных
conn = sqlite3.connect('db/shops.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц, если не существуют
def init_db():
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            payment_detail TEXT,
            owner_id VARCHAR,
            bot_api TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            name TEXT NOT NULL,
            description TEXT(500),
            price REAL,
            FOREIGN KEY(shop_id) REFERENCES shops(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username VARCHAR NOT NULL,
        role VARCHAR,
        tg_id INTEGER)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE,
            is_active BOOLEAN DEFAULT TRUE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_to_bots (
            shop_id INTEGER,
            bot_id INTEGER,
            PRIMARY KEY (shop_id, bot_id),
            FOREIGN KEY(shop_id) REFERENCES shops(id),
            FOREIGN KEY(bot_id) REFERENCES bots(id)
        )
    ''')
    conn.commit()

init_db()

# Функции для БД
def get_shops_by_tg_id(tg_id):
    return cursor.execute("SELECT id, name FROM shops WHERE owner_id = (?)", (tg_id,)).fetchall()

def add_user(message):
    username = message.from_user.username
    cursor.execute(f'''
        INSERT INTO users(username)
        VALUES('{username}');''')
    
    conn.commit()
    cursor.execute(f'''
        UPDATE users
        SET tg_id = '{message.from_user.id}'
        WHERE username = '{username}';
    ''')
    conn.commit()
    return 

def get_owner_by_shop_id(shop_id) -> int:
    return cursor.execute("SELECT tg_id FROM users INNER JOIN shops ON users.id = shops.owner_id WHERE shops.id = (?)", (shop_id,))



@bot.message_handler(commands=['start', 'help', 'Главное меню'])
def handle_start_help(message):
    help_text = (
        "Привет! Я бот-конструктор для магазинов.\n"
        "Команды:\n"
        "/newshop - Создать магазин\n"
        "/addproduct - Добавить товар\n"
        "/list_my_shops - Показать все магазины\n"
        "/listproducts - Показать товары магазина\n"
    )
    add_user(message)
    bot.send_message(message.chat.id, help_text)
    
@bot.message_handler(commands=['newshop'])
def create_shop(message):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
    btn1 = telebot.types.KeyboardButton('Ввести токен')
    btn2 = telebot.types.KeyboardButton('Ввести название')
    markup.add(btn1, btn2)
    msg = (
    "Введите название магазина, потом токен бота: Как получить токен?\n "
    "Краткая инструкция по созданию бота в BotFather (Telegram):\n"
    "1) Откройте Telegram и найдите @BotFather (официальный бот для создания ботов).\n"
    "2) Запустите бота и нажмите /start или /newbot.\n"
    "3) Укажите имя бота (отображаемое в чатах, например, TestBot).\n"
    "4) Придумайте юзернейм бота (должен заканчиваться на bot, например, TestExampleBot).\n"
    "5) Получите API-токен (сохраните, он нужен для управления ботом).\n"
    "6) Настройте бота через команды:\n"
    "7) /setdescription - описание бота.\n"
    "8) /setabouttext - краткая информация.\n"
    "9) /setcommands - список команд.\n"
    "10) Готово! Теперь можно программировать бота на Python (aiogram, python-telegram-bot), Node.js (telegraf) или других языках.\n"
    "🔹 Пример токена: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 (никому не передавайте!)\n")
    bot.send_message(message.chat.id, msg, reply_markup=markup)

# Временное хранилище данных
user_temp_data = {}

# Временное хранилище данных
user_temp_data = {}

@bot.message_handler(commands=['newshop'])
def handle_new_shop(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton('Ввести название')
    btn2 = types.KeyboardButton('Главное меню')
    markup.add(btn1, btn2)
    
    instructions = (
        "Для создания магазина:\n"
        "1. Введите название магазина\n"
        "2. Введите токен бота\n\n"
        "Как получить токен бота:\n"
        "- Найти @BotFather в Telegram\n"
        "- Создать нового бота командой /newbot\n"
        "- Скопировать полученный токен"
    )
    bot.send_message(message.chat.id, instructions, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text in ['Ввести токен', 'Ввести название'])
def handle_input_commands(message):
    if message.text == 'Ввести токен':
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        btn1 = types.KeyboardButton('Главное меню')
        btn2 = types.KeyboardButton('Назад')
        markup.add(btn1, btn2)
        msg = bot.send_message(message.chat.id, 'Введите токен бота:', reply_markup=markup)
        bot.register_next_step_handler(msg, process_token_input)
    elif message.text == 'Ввести название':
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        btn1 = types.KeyboardButton('Главное меню')
        btn2 = types.KeyboardButton('Назад')
        markup.add(btn1, btn2)
        msg = bot.send_message(message.chat.id, 'Введите название магазина:', reply_markup=markup)
        bot.register_next_step_handler(msg, process_name_input)

def process_token_input(message):
    if message.text in ['Главное меню', 'Назад']:
        handle_menu_actions(message)
        return
    
    if len(message.text) < 10:  # Минимальная проверка на валидность токена
        bot.send_message(message.chat.id, "Токен слишком короткий. Попробуйте еще раз.")
        return
    
    if message.chat.id not in user_temp_data:
        user_temp_data[message.chat.id] = {}
    
    user_temp_data[message.chat.id]['bot_api'] = message.text
    bot.send_message(message.chat.id, "Токен сохранен!")
    
    if 'name' in user_temp_data[message.chat.id]:
        complete_shop_creation(message)
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton('Ввести название'))
        bot.send_message(message.chat.id, 'Теперь введите название магазина:', reply_markup=markup)

def process_name_input(message):
    if message.text in ['Главное меню', 'Назад']:
        handle_menu_actions(message)
        return
    
    if len(message.text) < 2:  # Минимальная проверка на валидность названия
        bot.send_message(message.chat.id, "Название слишком короткое. Попробуйте еще раз.")
        return
    
    if message.chat.id not in user_temp_data:
        user_temp_data[message.chat.id] = {}
    
    user_temp_data[message.chat.id]['name'] = message.text
    bot.send_message(message.chat.id, "Название сохранено!")
    
    if 'bot_api' in user_temp_data[message.chat.id]:
        complete_shop_creation(message)
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton('Ввести токен'))
        bot.send_message(message.chat.id, 'Теперь введите токен бота:', reply_markup=markup)

def complete_shop_creation(message):
    shop_data = user_temp_data.get(message.chat.id, {})
    shop_name = shop_data.get('name')
    bot_token = shop_data.get('bot_api')
    
    if not shop_name:
        bot.send_message(message.chat.id, "Ошибка: название магазина не указано")
        return
    
    try:
        # Создаем запись о магазине
        cursor.execute('''
            INSERT INTO shops (name, owner_id, bot_api)
            VALUES (?, ?, ?)
        ''', (shop_name, message.from_user.id, bot_token))
        shop_id = cursor.lastrowid
        
        # Если есть токен, привязываем бота
        if bot_token:
            # Добавляем бота в таблицу bots
            cursor.execute('INSERT OR IGNORE INTO bots (token) VALUES (?)', (bot_token,))
            
            # Получаем ID бота
            cursor.execute('SELECT id FROM bots WHERE token = ?', (bot_token,))
            bot_id = cursor.fetchone()[0]
            
            # Создаем связь магазин-бот
            cursor.execute('''
                INSERT INTO shop_to_bots (shop_id, bot_id)
                VALUES (?, ?)
            ''', (shop_id, bot_id))
        
        conn.commit()
        bot.send_message(message.chat.id, f"✅ Магазин '{shop_name}' успешно создан!")
        
    except sqlite3.Error as e:
        conn.rollback()
        bot.send_message(message.chat.id, f"❌ Ошибка при создании магазина: {e}")
    finally:
        # Очищаем временные данные
        if message.chat.id in user_temp_data:
            del user_temp_data[message.chat.id]

def handle_menu_actions(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('Ввести токен'), types.KeyboardButton('Ввести название'))
    bot.send_message(message.chat.id, 'Выберите действие:', reply_markup=markup)

def link_bot_to_shop(shop_id, bot_token):
    try:
        # Сначала создаем или находим бота
        cursor.execute('INSERT OR IGNORE INTO bots (token) VALUES (?)', (bot_token,))
        
        # Получаем ID бота
        cursor.execute('SELECT id FROM bots WHERE token = ?', (bot_token,))
        bot_id = cursor.fetchone()[0]
        
        # Связываем магазин и бота
        cursor.execute('''
            INSERT OR IGNORE INTO shop_bots (shop_id, bot_id)
            VALUES (?, ?)
        ''', (shop_id, bot_id))
        
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Ошибка при привязке бота: {e}")
        return False


@bot.message_handler(commands=['list_my_shops'])
def list_my_shops(message):
    shops = get_shops_by_tg_id(message.from_user.id)
    owner_id = message.from_user.id
    if not shops:
        bot.send_message(message.chat.id, "Нет созданных магазинов.")
        return
    response = "Магазины:\n" + "\n".join([f"id владельца: {owner_id}\nНазвание: {shop[1]}" for shop in shops])
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['addproduct'])
def add_product(message):
    shops = get_shops_by_tg_id(message.from_user.id)
    if not shops:
        bot.send_message(message.chat.id, "Нет магазинов. Создайте магазин командой /newshop")
        return
    shop_list = "\n".join([f"{shop[0]}. {shop[1]}" for shop in shops])
    msg = bot.send_message(message.chat.id, f"Выберите магазин, указав его id:\n{shop_list}")
    bot.register_next_step_handler(msg, process_shop_selection_for_product)

def process_shop_selection_for_product(message):
    try:
        shop_id = int(message.text.strip())
        shop = cursor.execute("SELECT id FROM shops WHERE id=?", (shop_id,)).fetchone()
        if not shop:
            bot.send_message(message.chat.id, "Магазин не найден. Попробуйте снова.")
            return
        msg = bot.send_message(message.chat.id, "Введите название товара:")
        bot.register_next_step_handler(msg, save_product_name, shop_id)
    except ValueError:
        bot.send_message(message.chat.id, "Некорректный номер. Попробуйте снова.")

def save_product_name(message, shop_id):
    product_name = message.text.strip()
    if not product_name:
        bot.send_message(message.chat.id, "Название не может быть пустым.")
        return
    msg = bot.send_message(message.chat.id, "Введите цену товара:")
    bot.register_next_step_handler(msg, save_product_price, shop_id, product_name)

def save_product_price(message, shop_id, product_name):
    try:
        price = float(message.text.strip())
        cursor.execute(
            "INSERT INTO products (shop_id, name, price) VALUES (?, ?, ?)",
            (shop_id, product_name, price)
        )
        conn.commit()
        bot.send_message(message.chat.id, f"Товар '{product_name}' по цене {price} руб. добавлен.")
    except ValueError:
        bot.send_message(message.chat.id, "Некорректная цена.Попробуйте снова.")

# Показать товары выбранного магазина
@bot.message_handler(commands=['listproducts'])
def list_products(message):
    shops = cursor.execute("SELECT id, name FROM shops").fetchall()
    if not shops:
        bot.send_message(message.chat.id, "Нет магазинов.")
        return
    shop_list = "\n".join([f"{shop[0]}. {shop[1]}" for shop in shops])
    msg = bot.send_message(message.chat.id, f"Выберите магазин, указав его номер:\n{shop_list}")
    bot.register_next_step_handler(msg, show_products_for_shop)

def show_products_for_shop(message):
    try:
        shop_id = int(message.text.strip())
        shop_exists = cursor.execute("SELECT id FROM shops WHERE id=?", (shop_id,)).fetchone()
        if not shop_exists:
            bot.send_message(message.chat.id, "Магазин не найден.")
            return
        products = cursor.execute("SELECT name, price FROM products WHERE shop_id=?", (shop_id,)).fetchall()
        if not products:
            bot.send_message(message.chat.id, "Нет товаров для этого магазина.")
            return
        response = "Товары:\n" + "\n".join([f"{name} - {price} руб." for name, price in products])
        bot.send_message(message.chat.id, response)
    except ValueError:
        bot.send_message(message.chat.id, "Некорректный номер.")


bot.polling(non_stop=True)