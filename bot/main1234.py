import telebot
import sqlite3

# Вставьте сюда ваш токен бота
TOKEN = '8021455001:AAG5vnEOS7e6i0nRDyrWQ455maJEXr8DTa0'

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
            owner_id VARCHAR
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            name TEXT NOT NULL,
            price REAL,
            FOREIGN KEY(shop_id) REFERENCES shops(id)
        )
    ''')
    conn.commit()

init_db()


@bot.message_handler(commands=['start', 'help'])
def handle_start_help(message):
    help_text = (
        "Привет! Я бот-конструктор для магазинов.\n"
        "Команды:\n"
        "/newshop - Создать магазин\n"
        "/updateshop - Обновить магазин\n"
        "/addproduct - Добавить товар\n"
        "/list_my_shops - Показать все магазины\n"
        "/listproducts - Показать товары магазина\n"
    )
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

@bot.message_handler(content_types=['text'])
def get_text_messages(message):

    if message.text == 'Ввести токен':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn4 = telebot.types.KeyboardButton('Главное меню')
        btn5 = telebot.types.KeyboardButton('Назад')
        markup.add(btn4, btn5)
        bot.send_message(message.chat.id, 'Введити токен', reply_markup=markup) #ответ бота

    elif message.text == 'Ввести название':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn6 = telebot.types.KeyboardButton('Главное меню')
        btn7 = telebot.types.KeyboardButton('Назад')
        markup.add(btn6, btn7)
        bot.send_message(message.chat.id, 'Введите название', reply_markup=markup) #ответ бота

    elif message.text == 'Главное меню':
        help_textx = (
            "Привет! Я бот-конструктор для магазинов.\n"
            "Команды:\n"
            "/newshop - Создать магазин\n"
            "/updateshop - Обновить магазин\n"
            "/addproduct - Добавить товар\n"
            "/list_my_shops - Показать все магазины\n"
            "/listproducts - Показать товары магазина\n"
        )
        bot.send_message(message.chat.id, help_textx)

    elif message.text == '/updateshop':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('Товары')
        btn2 = telebot.types.KeyboardButton('Магазин')
        btn3 = telebot.types.KeyboardButton('Платежи')
        btn3 = telebot.types.KeyboardButton('Работники')
        markup.add(btn1, btn2, btn3)
        msg = ("Выберите что вам необходимо изменить")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Платежи':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('Поменять систему')
        btn2 = telebot.types.KeyboardButton('Указать Валюту')      
        markup.add(btn1, btn2)
        msg = ("Выберите что вам необходимо сделать")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Помянять систему':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn4 = telebot.types.KeyboardButton('Главное меню')
        btn5 = telebot.types.KeyboardButton('Назад')
        markup.add(btn4, btn5)
        bot.send_message(message.chat.id, 'Выбери систему', reply_markup=markup) #ответ бота
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Указать Валюту':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn4 = telebot.types.KeyboardButton('Главное меню')
        btn5 = telebot.types.KeyboardButton('Назад')
        markup.add(btn4, btn5)
        bot.send_message(message.chat.id, 'Выбери валюту', reply_markup=markup) #ответ бота

    elif message.text == 'Товары':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('Добавить')
        btn2 = telebot.types.KeyboardButton('Найти')
        btn3 = telebot.types.KeyboardButton('Менять')
        btn4 = telebot.types.KeyboardButton('Удалить')       
        markup.add(btn1, btn2, btn3, btn4)
        msg = ("Выберите что вам необходимо сделать")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Добавить':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn4 = telebot.types.KeyboardButton('Главное меню')
        btn5 = telebot.types.KeyboardButton('Назад')
        markup.add(btn4, btn5)
        bot.send_message(message.chat.id, '♦Добавить♦', reply_markup=markup) #ответ бота

    elif message.text == 'Магазин':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('Категории')
        btn2 = telebot.types.KeyboardButton('Менять ТОКЕН')
        btn3 = telebot.types.KeyboardButton('Менять Название')      
        markup.add(btn1, btn2, btn3)
        msg = ("Выберите что вам нужно менять")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Категории':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('Добавить')
        btn2 = telebot.types.KeyboardButton('Показать')
        btn3 = telebot.types.KeyboardButton('Название')    
        btn4 = telebot.types.KeyboardButton('Удалить')             
        markup.add(btn1, btn2, btn3, btn4)
        msg = ("Выберите что вам нужно менять")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Работники':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('Добавить админа')
        btn2 = telebot.types.KeyboardButton('Список')    
        btn3 = telebot.types.KeyboardButton('Уволить')             
        markup.add(btn1, btn2, btn3)
        msg = ("Выберите что вам нужно менять")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == 'Уволить':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('┼ТОЧНО?┼')      
        markup.add(btn1)
        msg = ("Выберите что вам нужно менять")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == '┼ТОЧНО?┼':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn1 = telebot.types.KeyboardButton('╤╧╨┼КАВО???┼╨╧╤')      
        markup.add(btn1)
        msg = ("Выберите кого послать на ***")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)

    elif message.text == '╤╧╨┼КАВО???┼╨╧╤':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True) #создание новых кнопок
        btn4 = telebot.types.KeyboardButton('Главное меню')
        markup.add(btn4)
        msg = ("Выберите кого послать на ***")
        bot.send_message(message.chat.id, msg, reply_markup=markup)
        #bot.register_next_step_handler(msg, message.chat.id, reply_markup=markup)


def save_shop(message):
    shop_name = message.text.strip()
    if shop_name:
        cursor.execute(f"INSERT INTO shops (name, owner_id) VALUES (?, ?)", (shop_name, message.from_user.id))
        conn.commit()
        bot.send_message(message.chat.id, f"Магазин '{shop_name}' создан.")
    else:
        bot.send_message(message.chat.id, "Название не может быть пустым.")


@bot.message_handler(commands=['list_my_shops'])
def list_shops(message):
    shops = cursor.execute("SELECT owner_id, name FROM shops WHERE owner_id = (?)", (message.from_user.id,)).fetchall()
    if not shops:
        bot.send_message(message.chat.id, "Нет созданных магазинов.")
        return
    response = "Магазины:\n" + "\n".join([f"id владельца: {shop[0]}\n {shop[1]}" for shop in shops])
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['addproduct'])
def add_product(message):
    shops = cursor.execute("SELECT id, name FROM shops").fetchall()
    if not shops:
        bot.send_message(message.chat.id, "Нет магазинов. Создайте магазин командой /newshop")
        return
    shop_list = "\n".join([f"{shop[0]}. {shop[1]}" for shop in shops])
    msg = bot.send_message(message.chat.id, f"Выберите магазин, указав его номер:\n{shop_list}")
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