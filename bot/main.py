import telebot
import sqlite3

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
        "/addproduct - Добавить товар\n"
        "/list_my_shops - Показать все магазины\n"
        "/listproducts - Показать товары магазина\n"
    )
    bot.send_message(message.chat.id, help_text)
    


@bot.message_handler(commands=['newshop'])
def create_shop(message):
    msg = bot.send_message(message.chat.id, "Введите название магазина:")
    bot.register_next_step_handler(msg, save_shop)
    

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