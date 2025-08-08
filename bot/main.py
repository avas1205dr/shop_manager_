import telebot
import sqlite3
import os
import shutil
import uuid
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
        description TEXT,
        price REAL,
        image_path TEXT,
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

if not os.path.exists('images'):
    os.makedirs('images')

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
    msg = bot.send_message(message.chat.id, "Введите описание товара (до 500 символов):")
    bot.register_next_step_handler(msg, save_product_description, shop_id, product_name)

def save_product_description(message, shop_id, product_name):
    description = message.text.strip()[:500]  # Обрезаем до 500 символов
    msg = bot.send_message(message.chat.id, "Введите цену товара:")
    bot.register_next_step_handler(msg, save_product_price, shop_id, product_name, description)

def save_product_price(message, shop_id, product_name, description):
    try:
        price = float(message.text.strip())
        msg = bot.send_message(message.chat.id, "Отправьте фотографию товара:")
        bot.register_next_step_handler(msg, save_product_photo, shop_id, product_name, description, price)
    except ValueError:
        bot.send_message(message.chat.id, "Некорректная цена. Попробуйте снова.")

def save_product_photo(message, shop_id, product_name, description, price):
    try:
        filepath = None
        
        if message.content_type == 'photo':
            # Получаем файл с самым высоким разрешением
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            # Генерируем уникальное имя файла
            file_ext = file_info.file_path.split('.')[-1]
            filename = f"{uuid.uuid4()}.{file_ext}"
            filepath = os.path.join('images', filename)
            
            # Сохраняем файл
            with open(filepath, 'wb') as new_file:
                new_file.write(downloaded_file)
        else:
            # Используем дефолтное изображение
            default_image = 'work_photos/default_not_image.png'
            if os.path.exists(default_image):
                # Копируем дефолтное изображение с уникальным именем
                file_ext = default_image.split('.')[-1]
                filename = f"{uuid.uuid4()}.{file_ext}"
                filepath = os.path.join('images', filename)
                
                import shutil
                shutil.copy(default_image, filepath)
            else:
                bot.send_message(message.chat.id, "Фото не получено, а дефолтное изображение не найдено.")
                return
        
        # Сохраняем данные в БД
        cursor.execute(
            "INSERT INTO products (shop_id, name, description, price, image_path) VALUES (?, ?, ?, ?, ?)",
            (shop_id, product_name, description, price, filepath)
        )
        conn.commit()
        
        # Формируем сообщение о добавлении товара
        response_msg = (
            f"Товар успешно добавлен!\n"
            f"Название: {product_name}\n"
            f"Описание: {description}\n"
            f"Цена: {price} руб.\n"
        )
        
        # Добавляем информацию о фото
        if message.content_type == 'photo':
            response_msg += "Фото: загружено пользователем"
        else:
            response_msg += "Фото: использовано стандартное изображение"
            
        bot.send_message(message.chat.id, response_msg)
        
    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")

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



# Обработчик callback-ов для пагинации и редактирования
@bot.callback_query_handler(func=lambda call: True)
def handle_product_callbacks(call):
    if call.data.startswith("products_page_"):
        # Обработка пагинации
        _, _, shop_id, page = call.data.split("_")
        show_products_for_shop(call.message, int(page))
        bot.answer_callback_query(call.id)
    elif call.data.startswith("edit_product_"):
        # Обработка кнопки редактирования
        product_id = call.data.split("_")[2]
        edit_product_menu(call.message, product_id)
        bot.answer_callback_query(call.id)
    elif call.data == "back_to_shops":
        # Возврат к списку магазинов
        shops = get_shops_by_tg_id(call.from_user.id)
        shop_list = "\n".join([f"{shop[0]}. {shop[1]}" for shop in shops])
        bot.send_message(call.message.chat.id, f"Ваши магазины:\n{shop_list}")
        bot.answer_callback_query(call.id)

def edit_product_menu(message, product_id):
    product = cursor.execute("SELECT id, name, price, description, image_path FROM products WHERE id=?", (product_id,)).fetchone()
    
    if not product:
        bot.send_message(message.chat.id, "Товар не найден.")
        return
    
    product_id, name, price, description, image_path = product
    
    # Создаем клавиатуру с опциями редактирования
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✏️ Изменить название", callback_data=f"edit_name_{product_id}"))
    markup.add(types.InlineKeyboardButton("💰 Изменить цену", callback_data=f"edit_price_{product_id}"))
    markup.add(types.InlineKeyboardButton("📝 Изменить описание", callback_data=f"edit_desc_{product_id}"))
    markup.add(types.InlineKeyboardButton("🖼️ Изменить фото", callback_data=f"edit_photo_{product_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ Удалить товар", callback_data=f"delete_product_{product_id}"))
    
    # Получаем shop_id для кнопки "Назад"
    shop_id = cursor.execute("SELECT shop_id FROM products WHERE id=?", (product_id,)).fetchone()[0]
    markup.add(types.InlineKeyboardButton("🔙 Назад к товарам", callback_data=f"products_page_{shop_id}_0"))
    
    # Отправляем сообщение с информацией о товаре
    response = f"📦 Редактирование товара:\n\n"
    response += f"🆔 ID: {product_id}\n"
    response += f"📌 Название: {name}\n"
    response += f"💰 Цена: {price} руб.\n"
    response += f"📝 Описание: {description if description else 'нет описания'}\n"
    
    # Пытаемся отправить фото товара, если оно есть
    try:
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as photo:
                bot.send_photo(message.chat.id, photo, caption=response, reply_markup=markup)
        else:
            bot.send_message(message.chat.id, response, reply_markup=markup)
    except Exception as e:
        bot.send_message(message.chat.id, response, reply_markup=markup)
        print(f"Ошибка при отправке фото: {e}")



@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_name_"))
def handle_edit_name(call):
    product_id = call.data.split("_")[2]
    msg = bot.send_message(call.message.chat.id, "Введите новое название товара:")
    bot.register_next_step_handler(msg, process_new_name, product_id)
    bot.answer_callback_query(call.id)

def process_new_name(message, product_id):
    new_name = message.text.strip()
    if not new_name:
        bot.send_message(message.chat.id, "Название не может быть пустым.")
        return
    
    cursor.execute("UPDATE products SET name=? WHERE id=?", (new_name, product_id))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Название товара обновлено!")
    edit_product_menu(message, product_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_price_"))
def handle_edit_price(call):
    product_id = call.data.split("_")[2]
    msg = bot.send_message(call.message.chat.id, "Введите новую цену товара:")
    bot.register_next_step_handler(msg, process_new_price, product_id)
    bot.answer_callback_query(call.id)

def process_new_price(message, product_id):
    try:
        new_price = float(message.text.strip())
        cursor.execute("UPDATE products SET price=? WHERE id=?", (new_price, product_id))
        conn.commit()
        bot.send_message(message.chat.id, "✅ Цена товара обновлена!")
        edit_product_menu(message, product_id)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Некорректная цена. Введите число.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_desc_"))
def handle_edit_desc(call):
    product_id = call.data.split("_")[2]
    msg = bot.send_message(call.message.chat.id, "Введите новое описание товара:")
    bot.register_next_step_handler(msg, process_new_desc, product_id)
    bot.answer_callback_query(call.id)

def process_new_desc(message, product_id):
    new_desc = message.text.strip()[:500]
    cursor.execute("UPDATE products SET description=? WHERE id=?", (new_desc, product_id))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Описание товара обновлено!")
    edit_product_menu(message, product_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_photo_"))
def handle_edit_photo(call):
    product_id = call.data.split("_")[2]
    msg = bot.send_message(call.message.chat.id, "Отправьте новое фото товара:")
    bot.register_next_step_handler(msg, process_new_photo, product_id)
    bot.answer_callback_query(call.id)

def process_new_photo(message, product_id):
    try:
        if message.content_type != 'photo':
            bot.send_message(message.chat.id, "Пожалуйста, отправьте фотографию.")
            return
        
        # Получаем текущий путь к фото
        old_image_path = cursor.execute("SELECT image_path FROM products WHERE id=?", (product_id,)).fetchone()[0]
        
        # Получаем новое фото
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Генерируем уникальное имя файла
        file_ext = file_info.file_path.split('.')[-1]
        filename = f"{uuid.uuid4()}.{file_ext}"
        new_image_path = os.path.join('images', filename)
        
        # Сохраняем новый файл
        with open(new_image_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        
        # Обновляем запись в БД
        cursor.execute("UPDATE products SET image_path=? WHERE id=?", (new_image_path, product_id))
        conn.commit()
        
        # Удаляем старое фото, если оно не дефолтное
        if old_image_path and os.path.exists(old_image_path) and 'default_not_image' not in old_image_path:
            try:
                os.remove(old_image_path)
            except Exception as e:
                print(f"Ошибка при удалении старого фото: {e}")
        
        bot.send_message(message.chat.id, "✅ Фото товара обновлено!")
        edit_product_menu(message, product_id)
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при обновлении фото: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_product_"))
def handle_delete_product(call):
    product_id = call.data.split("_")[2]
    
    # Получаем информацию о товаре для подтверждения
    product = cursor.execute("SELECT name FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден!")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{product_id}"),
        types.InlineKeyboardButton("❌ Нет, отмена", callback_data=f"cancel_delete_{product_id}")
    )
    
    bot.send_message(
        call.message.chat.id,
        f"Вы уверены, что хотите удалить товар '{product[0]}'?",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_"))
def handle_confirm_delete(call):
    product_id = call.data.split("_")[2]
    
    try:
        # Получаем путь к изображению для удаления
        image_path = cursor.execute("SELECT image_path FROM products WHERE id=?", (product_id,)).fetchone()[0]
        shop_id = cursor.execute("SELECT shop_id FROM products WHERE id=?", (product_id,)).fetchone()[0]
        
        # Удаляем запись из БД
        cursor.execute("DELETE FROM products WHERE id=?", (product_id,))
        conn.commit()
        
        # Удаляем изображение, если оно не дефолтное
        if image_path and os.path.exists(image_path) and 'default_not_image' not in image_path:
            try:
                os.remove(image_path)
            except Exception as e:
                print(f"Ошибка при удалении фото: {e}")
        
        bot.edit_message_text(
            "✅ Товар успешно удален!",
            call.message.chat.id,
            call.message.message_id
        )
        # Возвращаемся к списку товаров магазина
        show_products_for_shop(call.message, shop_id=shop_id, page=0)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка при удалении: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_delete_"))
def handle_cancel_delete(call):
    product_id = call.data.split("_")[2]
    bot.edit_message_text(
        "Удаление отменено.",
        call.message.chat.id,
        call.message.message_id
    )
    edit_product_menu(call.message, product_id)

# Обновленный обработчик для пагинации
@bot.callback_query_handler(func=lambda call: call.data.startswith("products_page_"))
def handle_products_pagination(call):
    try:
        parts = call.data.split("_")
        shop_id = parts[2]
        page = int(parts[3])
        show_products_for_shop(call.message, shop_id=shop_id, page=page)
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)}")

# Небольшая модификация функции show_products_for_shop для работы с shop_id как параметром
def show_products_for_shop(message, shop_id=None, page=0):
    try:
        if shop_id is None:
            shop_id = int(message.text.strip())
        
        shop_exists = cursor.execute("SELECT id FROM shops WHERE id=?", (shop_id,)).fetchone()
        if not shop_exists:
            bot.send_message(message.chat.id, "Магазин не найден.")
            return
        
        # Получаем все товары для магазина
        products = cursor.execute("SELECT id, name, price FROM products WHERE shop_id=? ORDER BY id", (shop_id,)).fetchall()
        
        if not products:
            bot.send_message(message.chat.id, "Нет товаров для этого магазина.")
            return
        
        # Разбиваем на страницы по 5 товаров
        products_per_page = 1
        total_pages = (len(products) // products_per_page) + (1 if len(products) % products_per_page else 0)
        start_index = page * products_per_page
        end_index = start_index + products_per_page
        current_products = products[start_index:end_index]
        
        # Создаем сообщение с товарами
        response = f"🛍️ Товары магазина (страница {page+1}/{total_pages}):\n\n"
        for product_id, name, price in current_products:
            response += f"🔹 {name} - {price} руб.\n"
        
        # Создаем клавиатуру с кнопками
        markup = types.InlineKeyboardMarkup()
        
        # Добавляем кнопки редактирования для каждого товара
        for product_id, name, price in current_products:
            markup.add(types.InlineKeyboardButton(
                text=f"✏️ {name}",
                callback_data=f"edit_product_{product_id}"
            ))
        
        # Добавляем кнопки пагинации
        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"products_page_{shop_id}_{page-1}"
            ))
        if page < total_pages - 1:
            pagination_buttons.append(types.InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=f"products_page_{shop_id}_{page+1}"
            ))
        
        if pagination_buttons:
            markup.row(*pagination_buttons)
        
        # Добавляем кнопку возврата
        markup.add(types.InlineKeyboardButton(
            text="🔙 Назад к магазинам",
            callback_data="back_to_shops"
        ))
        # Отправляем сообщение
        bot.send_message(message.chat.id, response, reply_markup=markup)
    except ValueError:
        bot.send_message(message.chat.id, "Некорректный номер.")

bot.polling(non_stop=True)