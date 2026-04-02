import telebot
import threading
import logging
import os
import uuid
import sqlite3
import time

import database as database
import keyboards as keyboards
import shop_bot
from states import UserState
from telebot import types
import config

logging.basicConfig(level=logging.ERROR)

user_product_messages = {}
active_shop_bots = {}
user_states = {}

bot = telebot.TeleBot(config.BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start_handler(message):
    database.add_user(message.from_user.id, message.from_user.username)
    user_id = message.from_user.id
    user_states[user_id] = UserState.MAIN_MENU
    
    welcome_text = """🛍️ Добро пожаловать в Shop Manager Bot!

Этот бот поможет вам создать и управлять собственными магазинными ботами в Telegram.

Для того чтобы настроить ваш магазин нужно:
• Создать магазин в нашем боте
• Создать бота через @BotFather, скопировать API-токен и вставить в разделе API бота
• Также в @BotFather настроить Payments (инструкция есть в разделе Paymaster) и отправить токен
• Добавить работников, товары, категории и т.д.
• Готово!


Возможности:
• Создание неограниченного количества магазинов
• Управление товарами и категориями
• Настройка способов оплаты
• Просмотр отзывов и рейтингов

Выберите действие:"""
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=keyboards.create_main_menu())

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

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_product_"))
def show_manager_product(call):
    parts = call.data.split("_")
    if len(parts) < 5:
        bot.answer_callback_query(call.id, "Неверные данные")
        return
    product_id = int(parts[2])
    category_id = int(parts[3])
    page = int(parts[4])
    product = database.get_product_info(product_id)
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден")
        return
    
    (prod_id, cat_id, name, desc, price, image_path, is_digital, created_at, popularity_score) = product
    text = f"{name}\nЦена: {price}₽\nОписание: {desc or 'Нет'}"
    markup = keyboards.create_edit_product_menu(product_id, category_id, page)
    
    bot.delete_message(call.message.chat.id, call.message.message_id)
    
    if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
        with open(image_path, 'rb') as photo:
            bot.send_photo(call.message.chat.id, photo, caption=text, reply_markup=markup)
    else:
        bot.send_message(call.message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("back_to_products_"))
def handle_back_to_products(call):
    parts = call.data.split("_")
    category_id = int(parts[3])
    page = int(parts[4])
    
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.send_message(
        call.message.chat.id,
        "📦 Товары в разделе:",
        reply_markup=keyboards.create_products_menu(category_id, page)
    )

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
    is_digital = user_states.get(f"{user_id}_product_is_digital", True)
    
    image_path = "work_photos/default_not_image.jpg"
    product_id = database.add_product(category_id, product_name, product_price, image_path, is_digital, description)
    
    if product_id:
        bot.answer_callback_query(call.id, "✅ Товар добавлен со стандартным изображением")
        bot.send_message(
            call.message.chat.id,
            "📦 Товары в разделе:",
            reply_markup=keyboards.create_products_menu(category_id))
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при добавлении товара")
    
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
    is_digital = user_states.get(f"{user_id}_product_is_digital", True)
    
    product_id = database.add_product(category_id, product_name, product_price, None, is_digital, description)
    
    if product_id:
        bot.answer_callback_query(call.id, "✅ Товар добавлен без изображения")
        bot.send_message(
            call.message.chat.id,
            "📦 Товары в разделе:",
            reply_markup=keyboards.create_products_menu(category_id))
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при добавлении товара")
    
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
    
    
@bot.callback_query_handler(func=lambda call: call.data.startswith("paymaster_token_"))
def paymaster_token_handler(call):
    shop_id = int(call.data.split("_")[-1])
    user_states[call.from_user.id] = UserState.EDITING_PAYMASTER
    user_states[f"{call.from_user.id}_shop_id"] = shop_id
    
    instruction_text = """💳 Настройка PayMaster:

1. Перейдите в @BotFather
2. Выберите вашего бота
3. Перейдите в раздел "Payments"
4. Выберите "PayMaster" как провайдера платежей
5. Скопируйте полученный токен

Отправьте токен в следующем сообщении или 'назад' для отмены:"""
    
    bot.edit_message_text(
        instruction_text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
    )

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.EDITING_PAYMASTER)
def save_paymaster_token(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    token = message.text.strip()
    
    if token.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        bot.send_message(
            message.chat.id,
            "❌ Настройка PayMaster отменена",
            reply_markup=keyboards.create_shop_management_menu(shop_id))
        return
    
    if len(token) < 10:
        bot.send_message(message.chat.id, "❌ Токен слишком короткий. Попробуйте снова или отправьте 'назад' для отмены")
        return
    
    if database.update_paymaster_token(shop_id, token):
        bot.send_message(
            message.chat.id,
            "✅ PayMaster токен успешно сохранен!",
            reply_markup=keyboards.create_shop_management_menu(shop_id))
    else:
        bot.send_message(message.chat.id, "❌ Ошибка при сохранении токена")
    
    user_states[user_id] = UserState.SHOP_MENU

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
            reply_markup=keyboards.create_shop_management_menu(shop_id))
        return
    
    admin_user_id = None
    username = None
    if admin_input.startswith('@'):
        username = admin_input[1:]
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT tg_id FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        
        if result:
            admin_user_id = result[0]
        else:
            cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
            admin_user_id = cursor.lastrowid
            conn.commit()
        conn.close()
    else:
        try:
            admin_user_id = int(admin_input)
            conn = sqlite3.connect(database.DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute("SELECT tg_id, username FROM users WHERE tg_id = ?", (admin_user_id,))
            result = cursor.fetchone()
            if result:
                username = result[1]
            else:
                database.add_user(admin_user_id)
                conn.commit()
        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите корректный @username или ID пользователя")
            return
        finally:
            conn.close()
    
    if not admin_user_id:
        bot.send_message(message.chat.id, "❌ Не удалось определить пользователя")
        return
    
    shop_info = database.get_shop_info(shop_id)
    if not shop_info:
        bot.send_message(message.chat.id, "❌ Магазин не найден")
        return
    
    if shop_info[1] != user_id:
        bot.send_message(message.chat.id, "❌ Только создатель магазина может добавлять работников")
        return
    
    if admin_user_id == shop_info[1]:
        bot.send_message(message.chat.id, "❌ Вы уже являетесь создателем этого магазина")
        return
    
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR IGNORE INTO shop_admins (shop_id, user_id) 
        VALUES (?, ?)
    """, (shop_id, admin_user_id))
    
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    if affected_rows > 0:
        worker_message = f"🎉 Вы были добавлены как работник магазина '{shop_info[2]}'!\n\nТеперь вы можете управлять этим магазином через @{bot.get_me().username}"
        try:
            bot.send_message(admin_user_id, worker_message)
        except:
            pass
        
        admins = database.get_shop_workers(shop_id)
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
            reply_markup=keyboards.create_shop_management_menu(shop_id))
    else:
        bot.send_message(
            message.chat.id,
            f"ℹ️ Пользователь {admin_input} уже является работником",
            reply_markup=keyboards.create_shop_management_menu(shop_id))
    
    user_states[user_id] = UserState.SHOP_MENU

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
            reply_markup=keyboards.create_shop_management_menu(shop_id))
        return
    
    if ":" not in credentials:
        bot.send_message(message.chat.id, "❌ Формат неверный. Введите ShopID:SecretKey или 'назад' для отмены")
        return
    
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE shops SET payment_method = 'online', yookassa_credentials = ? WHERE id = ?", (credentials, shop_id))
    conn.commit()
    conn.close()
    
    bot.send_message(
        message.chat.id,
        "✅ Настройки ЮKassa сохранены!",
        reply_markup=keyboards.create_shop_management_menu(shop_id))
    user_states[user_id] = UserState.SHOP_MENU

@bot.callback_query_handler(func=lambda call: call.data.startswith("broadcast_"))
def broadcast_menu_handler(call):
    shop_id = int(call.data.split("_")[-1])

    user_states[call.from_user.id] = UserState.BROADCAST_MESSAGE
    user_states[f"{call.from_user.id}_shop_id"] = shop_id
    
    count = len(database.get_shop_user_ids(shop_id))
    
    bot.edit_message_text(
        f"📢 Рассылка сообщений\n\nПодписчиков бота: {count}\n\n"
        "Отправьте сообщение (текст, фото или видео), которое хотите разослать всем пользователям вашего бота.\n"
        "Отправьте 'назад' для отмены.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
    )

@bot.message_handler(content_types=['text', 'photo', 'video', 'document'], 
                     func=lambda message: user_states.get(message.from_user.id) == UserState.BROADCAST_MESSAGE)
def execute_broadcast(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    
    if message.text and message.text.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        bot.send_message(message.chat.id, "Рассылка отменена", 
                        reply_markup=keyboards.create_shop_management_menu(shop_id))
        return

    if shop_id not in active_shop_bots:
        bot.send_message(message.chat.id, "❌ Бот магазина не запущен. Проверьте токен.")
        return
        
    shop_bot_instance = active_shop_bots[shop_id]
    users = database.get_shop_user_ids(shop_id)
    
    if not users:
        bot.send_message(message.chat.id, "❌ Нет пользователей для рассылки.")
        return

    bot.send_message(message.chat.id, f"🚀 Начинаю рассылку для {len(users)} пользователей...")
    
    def send_broadcast_thread():
        success_count = 0
        fail_count = 0
        cached_photo_id = None
        cached_video_id = None
        cached_document_id = None
        file_data = None
        
        try:
            if message.content_type == 'photo':
                file_info = bot.get_file(message.photo[-1].file_id)
                file_data = bot.download_file(file_info.file_path)
            elif message.content_type == 'video':
                file_info = bot.get_file(message.video.file_id)
                file_data = bot.download_file(file_info.file_path)
            elif message.content_type == 'document':
                file_info = bot.get_file(message.document.file_id)
                file_data = bot.download_file(file_info.file_path)
        except Exception as e:
            logging.error(f"Error downloading file from manager bot: {e}")
            bot.send_message(user_id, "❌ Ошибка при скачивании файла для рассылки.")
            return

        for uid in users:
            try:
                sent_msg = None
                if message.content_type == 'photo':
                    if cached_photo_id:
                        shop_bot_instance.send_photo(uid, cached_photo_id, caption=message.caption)
                    else:
                        sent_msg = shop_bot_instance.send_photo(uid, file_data, caption=message.caption)
                        cached_photo_id = sent_msg.photo[-1].file_id

                elif message.content_type == 'video':
                    if cached_video_id:
                        shop_bot_instance.send_video(uid, cached_video_id, caption=message.caption)
                    else:
                        sent_msg = shop_bot_instance.send_video(uid, file_data, caption=message.caption)
                        cached_video_id = sent_msg.video.file_id

                elif message.content_type == 'document':
                    if cached_document_id:
                        shop_bot_instance.send_document(uid, cached_document_id, caption=message.caption)
                    else:
                        sent_msg = shop_bot_instance.send_document(uid, file_data, caption=message.caption)
                        cached_document_id = sent_msg.document.file_id

                elif message.content_type == 'text':
                    shop_bot_instance.send_message(uid, message.text)
                
                success_count += 1
                time.sleep(0.05) 
            except Exception as e:
                logging.error(f"Failed to send broadcast to {uid}: {e}")
                fail_count += 1
        
        try:
            bot.send_message(user_id, 
                             f"✅ Рассылка завершена!\n"
                             f"Успешно: {success_count}\n"
                             f"Ошибок (блокировок): {fail_count}",
                             reply_markup=keyboards.create_shop_management_menu(shop_id))
        except:
            pass
            
    threading.Thread(target=send_broadcast_thread, daemon=True).start()
    
    user_states[user_id] = UserState.SHOP_MENU

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.ADDING_PROMO_CODE)
def handle_promo_code_input(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    text = message.text.strip()

    if text.lower() == "назад":
        user_states[user_id] = UserState.SHOP_MENU
        promos = database.get_shop_promocodes(shop_id)
        header = "🎟️ Промокоды магазина\n\nНажмите на промокод — он удалится." if promos else "🎟️ Промокодов пока нет."
        bot.send_message(message.chat.id, header,
                         reply_markup=keyboards.create_promocodes_menu(shop_id, promos))
        return

    code = text.upper()
    if len(code) < 2 or len(code) > 20:
        bot.send_message(message.chat.id, "❌ Код должен быть от 2 до 20 символов. Попробуйте снова:")
        return

    user_states[f"{user_id}_promo_code"] = code
    bot.send_message(
        message.chat.id,
        f"Код: <b>{code}</b>\n\nШаг 2/3: Выберите тип скидки:",
        parse_mode="HTML",
        reply_markup=keyboards.create_promo_type_menu(shop_id)
    )
    # Остаёмся в ADDING_PROMO_CODE — следующий шаг через кнопку


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == UserState.ADDING_PROMO_VALUE)
def handle_promo_value_input(message):
    user_id = message.from_user.id
    shop_id = user_states.get(f"{user_id}_shop_id")
    text = message.text.strip()

    if text.lower() == "назад":
        user_states[user_id] = UserState.ADDING_PROMO_CODE
        bot.send_message(message.chat.id, "Введите текст промокода:",
                         reply_markup=keyboards.create_back_button_menu(f"manage_promocodes_{shop_id}"))
        return

    try:
        value = float(text)
        dtype = user_states.get(f"{user_id}_promo_type", "percent")
        if value <= 0 or (dtype == "percent" and value > 100):
            raise ValueError()
    except ValueError:
        bot.send_message(message.chat.id, "❌ Некорректное значение. Введите положительное число:")
        return

    code = user_states.get(f"{user_id}_promo_code")
    dtype = user_states.get(f"{user_id}_promo_type", "percent")
    ok = database.create_promocode(shop_id, code, dtype, value)

    if not ok:
        bot.send_message(message.chat.id, f"❌ Промокод {code} уже существует. Попробуйте другой код:")
        user_states[user_id] = UserState.SHOP_MENU
        return

    discount_str = f"-{int(value)}%" if dtype == "percent" else f"-{int(value)}₽"
    promos = database.get_shop_promocodes(shop_id)
    bot.send_message(
        message.chat.id,
        f"✅ Промокод <b>{code}</b> ({discount_str}) создан!",
        parse_mode="HTML",
        reply_markup=keyboards.create_promocodes_menu(shop_id, promos)
    )
    user_states[user_id] = UserState.SHOP_MENU

    # ── Предупреждение о бесплатных товарах ──
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name, p.price, p.sale_price
        FROM products p
        JOIN categories c ON p.category_id = c.id
        WHERE c.shop_id = ?
    """, (shop_id,))
    rows = cursor.fetchall()
    conn.close()

    free_products = []
    for prod_id, prod_name, price, sale_price in rows:
        # Реальная цена — акционная если есть и она меньше базовой
        if sale_price is not None and 0 < sale_price < price:
            effective_price = sale_price
        else:
            effective_price = price

        if dtype == "percent":
            final = effective_price * (1 - value / 100)
        else:
            final = effective_price - value

        if final < 1.0:
            label = f"{prod_name} ({effective_price}₽)" if effective_price == price else f"{prod_name} ({price}₽ → {effective_price}₽ по акции)"
            free_products.append(label)

    if free_products:
        lines = "\n".join(f"• {label}" for label in free_products)
        bot.send_message(
            message.chat.id,
            f"⚠️ <b>Внимание!</b> С промокодом <b>{code}</b> ({discount_str}) "
            f"следующие товары станут <b>бесплатными</b> (итоговая цена &lt;1₽):\n\n"
            f"{lines}\n\n"
            f"Покупатели смогут получить их без оплаты. "
            f"Если это не задумано — удалите промокод или поднимите цены на эти товары.",
            parse_mode="HTML"
        )

    # ── Авто-рассылка через бот магазина ──
    if shop_id in active_shop_bots:
        shop_info = database.get_shop_info(shop_id)
        shop_name = shop_info[2] if shop_info else "магазин"
        users = database.get_shop_user_ids(shop_id)
        if users:
            promo_msg = (
                "🎉 Акция в магазине <b>" + shop_name + "</b>!\n\n"
                "🎟️ Промокод: <b>" + code + "</b>\n"
                "💸 Скидка: <b>" + discount_str + "</b>\n\n"
                "Введите промокод при оформлении заказа!"
            )
            shop_bot_instance = active_shop_bots[shop_id]

            def _broadcast_promo(bot_inst=shop_bot_instance, msg=promo_msg, uid_list=users):
                for uid in uid_list:
                    try:
                        bot_inst.send_message(uid, msg, parse_mode="HTML")
                        time.sleep(0.05)
                    except Exception:
                        pass

            threading.Thread(target=_broadcast_promo, daemon=True).start()
            bot.send_message(message.chat.id,
                             f"📢 Рассылка акции запущена для {len(users)} пользователей.")


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
                reply_markup=keyboards.create_main_menu()
            )
        
        elif data == "reviews":
            bot.edit_message_text(
                "📊 Рейтинг магазинов\n\nМагазины отсортированы по рейтингу:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_reviews_menu()
            )
        
        elif data.startswith("reviews_page_"):
            page = int(data.split("_")[-1])
            bot.edit_message_text(
                "📊 Рейтинг магазинов\n\nМагазины отсортированы по рейтингу:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_reviews_menu(page)
            )
        
        elif data.startswith("shop_detail_"):
            shop_id = int(data.split("_")[-1])
            show_shop_detail(call, shop_id)
        
        elif data == "my_shops":
            bot.edit_message_text(
                "🏪 Ваши магазины:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_my_shops_menu(user_id)
            )
        
        elif data == "create_shop":
            user_states[user_id] = UserState.CREATING_SHOP
            bot.edit_message_text(
                "Введите название для нового магазина (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu("my_shops")
            )
        
        elif data.startswith("manage_shop_"):
            shop_id = int(data.split("_")[-1])
            show_shop_management(call, shop_id)
        
        elif data.startswith("edit_token_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.EDITING_TOKEN
            user_states[f"{user_id}_shop_id"] = shop_id
            
            shop_info = database.get_shop_info(shop_id)
            current_token = shop_info[3] if shop_info and shop_info[3] else "Не установлен"
            
            bot.edit_message_text(
                f"🔑 Токен API бота\n\nТекущий токен: {current_token}\n\nВведите новый токен (минимум 30 символов):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}"))
        
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
                reply_markup=keyboards.create_categories_menu(shop_id))
        
        elif data.startswith("create_category_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.CREATING_CATEGORY
            user_states[f"{user_id}_shop_id"] = shop_id
            
            bot.edit_message_text(
                "Введите название для нового раздела (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"manage_products_{shop_id}"))
        
        elif data.startswith("category_"):
            category_id = int(data.split("_")[-1])
            bot.edit_message_text(
                "📦 Действия с разделом:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_category_actions_menu(category_id))
        
        elif data.startswith("view_products_"):
            category_id = int(data.split("_")[-1])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_products_menu(category_id))
        
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
                conn = sqlite3.connect(database.DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE shops SET payment_method = ? WHERE id = ?", (payment_type, shop_id))
                conn.commit()
                conn.close()
                bot.edit_message_text(
                    "✅ Способ оплаты установлен: Оплата на месте",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_shop_management_menu(shop_id))
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
                    reply_markup=keyboards.create_back_button_menu(f"payment_method_{shop_id}"))
        
        elif data.startswith("edit_welcome_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.EDITING_WELCOME
            user_states[f"{user_id}_shop_id"] = shop_id
            
            bot.edit_message_text(
                "Введите новое приветственное сообщение для покупателей (минимум 5 символов):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}"))
        
        elif data.startswith("delete_shop_"):
            shop_id = int(data.split("_")[-1])
            shop_info = database.get_shop_info(shop_id)
            if not shop_info:
                bot.answer_callback_query(call.id, "Магазин не найден")
                return
                
            if shop_info[1] != user_id:
                bot.answer_callback_query(call.id, "Только создатель магазина может его удалить")
                return
                
            conn = sqlite3.connect(database.DB_NAME)
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
                reply_markup=keyboards.create_my_shops_menu(user_id))
        
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
                reply_markup=keyboards.create_workers_menu(shop_id))
        
        elif data.startswith("add_worker_"):
            shop_id = int(data.split("_")[2])
            user_states[user_id] = UserState.ADDING_WORKER
            user_states[f"{user_id}_shop_id"] = shop_id
            bot.edit_message_text(
                "👤 Добавление работника\n\n"
                "Введите @username или ID пользователя\n\n",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"workers_{shop_id}"))
        
        elif data.startswith("list_workers_"):
            shop_id = int(data.split("_")[2])
            workers = database.get_shop_workers(shop_id)
            if not workers:
                bot.edit_message_text(
                    "В магазине нет работников",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_workers_menu(shop_id))
                return
            
            response = "👥 Список работников:\n\n"
            for worker_id, username in workers:
                response += f"• @{username} (ID: {worker_id})\n" if username else f"• ID: {worker_id}\n"
            
            bot.edit_message_text(
                response,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_workers_menu(shop_id))
        
        elif data.startswith("remove_worker_"):
            shop_id = int(data.split("_")[2])
            workers = database.get_shop_workers(shop_id)
            shop_info = database.get_shop_info(shop_id)
            owner_id = shop_info[1]
            workers = [(w[0], w[1]) for w in workers if w[0] != owner_id]
            
            if not workers:
                bot.answer_callback_query(call.id, "Нет работников для увольнения")
                return
            
            bot.edit_message_text(
                "Выберите работника для увольнения:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_remove_worker_menu(shop_id, workers))
        
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
                    reply_markup=keyboards.create_confirm_remove_step2_menu(shop_id, worker_id))
            else:
                shop_id = int(parts[2])
                worker_id = int(parts[3])
                bot.edit_message_text(
                    "Вы уверены, что хотите уволить этого работника (может не надо)?",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_confirm_remove_menu(shop_id, worker_id))
        
        elif data.startswith("do_remove_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            shop_id = int(parts[2])
            worker_id = int(parts[3])
            conn = sqlite3.connect(database.DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM users WHERE tg_id = ?", (worker_id,))
            result = cursor.fetchone()
            cursor.execute(f"""
                DELETE FROM shop_admins
                WHERE user_id = ? AND shop_id = ?
            """, (worker_id, shop_id))
            username = result[0] if result else None
            conn.close()
            shop_info = database.get_shop_info(shop_id)
            if database.remove_worker(shop_id, worker_id):
                worker_message = f"Вы были удалены как работник из магазина '{shop_info[2]}'"
                try:
                    bot.send_message(worker_id, worker_message)
                except:
                    pass
                
                admins = database.get_shop_workers(shop_id)
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
                reply_markup=keyboards.create_workers_menu(shop_id))
        
        elif data.startswith("view_orders_"):
            shop_id = int(data.split("_")[-1])
            orders = database.get_shop_orders(shop_id)
            
            if not orders:
                bot.edit_message_text(
                    "📋 Заказы\n\nЗаказов пока нет",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}"))
                return
            
            text = "📋 Заказы\n\n"
            markup = keyboards.create_orders_menu(shop_id, orders)
            
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup)

        elif data.startswith("orders_page_"):
            parts = data.split("_")
            shop_id = int(parts[2])
            page = int(parts[3])
            orders = database.get_shop_orders(shop_id)
            
            text = "📋 Заказы\n\n"
            markup = keyboards.create_orders_menu(shop_id, orders, page)
            
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup)

        elif data.startswith("order_detail_"):
            order_id = int(data.split("_")[-1])
            # Здесь можно реализовать просмотр деталей заказа
            bot.answer_callback_query(call.id, "Функция в разработке")
        
        elif data.startswith("edit_category_name_"):
            category_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.EDITING_CATEGORY_NAME
            user_states[f"{user_id}_category_id"] = category_id
            bot.edit_message_text(
                "Введите новое название для раздела (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"category_{category_id}"))
        
        elif data.startswith("delete_category_"):
            category_id = int(data.split("_")[-1])
            if database.delete_category(category_id):
                shop_id = database.get_shop_id_by_category(category_id)
                bot.edit_message_text(
                    "✅ Раздел удалён",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_categories_menu(shop_id))
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
                reply_markup=keyboards.create_edit_product_menu(product_id, category_id, page))
        
        elif data.startswith("prev_page_"):
            parts = data.split("_")
            category_id = int(parts[2])
            page = int(parts[3])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        elif data.startswith("next_page_"):
            parts = data.split("_")
            category_id = int(parts[2])
            page = int(parts[3])
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        elif data.startswith("edit_name_") or data.startswith("edit_price_") or data.startswith("edit_desc_") or data.startswith("edit_photo_") or data.startswith("edit_sale_"):
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
            
            product_info = database.get_product_info(int(user_states.get(f"{user_id}_product_id", 0) or 0))
            sale_hint = f"Текущая: {product_info[4]}₽" if product_info else ""
            prompt = {
                "name": "Введите новое название (мин 2 символа):\n\nОтправьте 'назад' для отмены",
                "price": "Введите новую цену (положительное число):\n\nОтправьте 'назад' для отмены",
                "desc": "Введите новое описание:\n\nОтправьте 'назад' для отмены",
                "photo": "Отправьте новое фото или текст 'пропустить'/'стандартное':\n\nОтправьте 'назад' для отмены",
                "sale": f"💸 Акционная цена ({sale_hint})\n\nВведите новую цену меньше обычной.\nЧтобы убрать скидку — отправьте '-'\n\nОтправьте 'назад' для отмены"
            }[edit_type]
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            bot.send_message(
                call.message.chat.id,
                prompt,
                reply_markup=keyboards.create_back_button_menu(f"product_{product_id}_{category_id}_{page}")
            )
        
        elif data.startswith("delete_product_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            product_id = int(parts[2])
            category_id = int(parts[3])
            page = int(parts[4])
            database.delete_product(product_id)
            bot.answer_callback_query(call.id, "✅ Товар удалён")
            bot.edit_message_text(
                "📦 Товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        elif data.startswith("back_to_products_"):
            parts = data.split("_")
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Неверные данные")
                return
            category_id = int(parts[3])
            page = int(parts[4])
            bot.edit_message_text(
                "📦 Т товары в разделе:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        elif data.startswith("all_products_"):
            shop_id = int(data.split("_")[-1])
            products = database.get_all_shop_products(shop_id)
            if not products:
                bot.edit_message_text(
                    "В магазине нет товаров.",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}"))
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

        # ─── ПРОМОКОДЫ ───────────────────────────────────────────────────
        elif data.startswith("manage_promocodes_"):
            shop_id = int(data.split("_")[-1])
            promos = database.get_shop_promocodes(shop_id)
            header = "🎟️ Промокоды магазина\n\nНажмите на промокод — он удалится." if promos else "🎟️ Промокодов пока нет."
            bot.edit_message_text(
                header,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_promocodes_menu(shop_id, promos)
            )

        elif data.startswith("add_promocode_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.ADDING_PROMO_CODE
            user_states[f"{user_id}_shop_id"] = shop_id
            bot.edit_message_text(
                "🎟️ Создание промокода\n\nШаг 1/3: Введите текст кода (например SALE20):\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"manage_promocodes_{shop_id}")
            )

        elif data.startswith("promo_type_percent_") or data.startswith("promo_type_fixed_"):
            parts = data.split("_")
            shop_id = int(parts[-1])
            dtype = "percent" if "percent" in data else "fixed"
            user_states[f"{user_id}_promo_type"] = dtype
            user_states[user_id] = UserState.ADDING_PROMO_VALUE
            hint = "(0–100, например 15 = скидка 15%)" if dtype == "percent" else "(в рублях, например 200 = скидка 200₽)"
            bot.edit_message_text(
                f"Шаг 3/3: Введите размер скидки {hint}:\n\nОтправьте 'назад' для отмены",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_back_button_menu(f"manage_promocodes_{shop_id}")
            )

        elif data.startswith("delete_promo_"):
            parts = data.split("_")
            promo_id = int(parts[2])
            shop_id = int(parts[3])
            database.deactivate_promocode(promo_id)
            bot.answer_callback_query(call.id, "✅ Промокод удалён")
            promos = database.get_shop_promocodes(shop_id)
            header = "🎟️ Промокоды магазина\n\nНажмите на промокод — он удалится." if promos else "🎟️ Промокодов пока нет."
            bot.edit_message_text(
                header,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboards.create_promocodes_menu(shop_id, promos)
            )

    except telebot.apihelper.ApiTelegramException as e:
        if e.error_code == 400 and 'message is not modified' in str(e):
            pass
        else:
            logging.error(f"Callback error: {str(e)}")
            bot.answer_callback_query(call.id, "Произошла ошибка. Попробуйте снова.")
    except Exception as e:
        logging.error(f"Callback error: {str(e)}")
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)[:100]}")

def show_shop_detail(call, shop_id):
    shop_info = database.get_shop_info(shop_id)
    if not shop_info:
        bot.answer_callback_query(call.id, "Магазин не найден")
        return
    
    shop_name = shop_info[2]
    
    conn = sqlite3.connect(database.DB_NAME)
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
    shop_info = database.get_shop_info(shop_id)
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
        reply_markup=keyboards.create_shop_management_menu(shop_id))

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
        reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}"))

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
            reply_markup=keyboards.create_shop_management_menu(shop_id))
        return
    
    if len(welcome_message) < 5:
        bot.send_message(message.chat.id, "❌ Сообщение слишком короткое (мин. 5 символов). Попробуйте снова или отправьте 'назад' для отмены")
        return
    
    if database.update_welcome_message(shop_id, welcome_message):
        bot.send_message(
            message.chat.id,
            "✅ Приветственное сообщение обновлено!",
            reply_markup=keyboards.create_shop_management_menu(shop_id))
        user_states[user_id] = UserState.SHOP_MENU
    else:
        bot.send_message(message.chat.id, "❌ Ошибка при обновлении сообщения")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_payment_"))
def set_payment_handler(call):
    shop_id = int(call.data.split("_")[-1])
    payment_type = "cash_on_delivery" if call.data.startswith("set_payment_cash_") else "online"
    
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE shops SET payment_method = ? WHERE id = ?", (payment_type, shop_id))
    conn.commit()
    conn.close()
    
    bot.edit_message_text(
        f"✅ Способ оплаты установлен: {'Оплата на месте' if payment_type == 'cash_on_delivery' else 'Онлайн-оплата'}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.create_shop_management_menu(shop_id))

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
                reply_markup=keyboards.create_main_menu())
            return
            
        if len(shop_name) < 2:
            bot.send_message(message.chat.id, "❌ Название магазина должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        shop_id = database.create_shop(user_id, shop_name)
        if not shop_id:
            bot.send_message(message.chat.id, "❌ Ошибка при создании магазина")
            return
            
        user_states[user_id] = UserState.SHOP_MENU
        
        bot.send_message(
            message.chat.id,
            f"✅ Магазин '{shop_name}' успешно создан!\n\nТеперь настройте его:",
            reply_markup=keyboards.create_shop_management_menu(shop_id))
    
    elif user_state == UserState.EDITING_TOKEN:
        token = message.text.strip()
        shop_id = user_states.get(f"{user_id}_shop_id")
        
        if token.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            bot.send_message(
                message.chat.id,
                "❌ Изменение токена отменено",
                reply_markup=keyboards.create_shop_management_menu(shop_id))
            return
            
        if len(token) < 30:
            bot.send_message(message.chat.id, "❌ Токен должен содержать не менее 30 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        bot_username = database.update_shop_token(shop_id, token)
        user_states[user_id] = UserState.SHOP_MENU
        if bot_username:
            bot.send_message(
                message.chat.id,
                "✅ Токен успешно обновлен!",
                reply_markup=keyboards.create_shop_management_menu(shop_id))
            
            if shop_id in active_shop_bots:
                try:
                    active_shop_bots[shop_id].stop_polling()
                except:
                    pass
                del active_shop_bots[shop_id]
            
            threading.Thread(target=shop_bot.run_shop_bot, args=(shop_id, token, database.get_shop_info(shop_id)[5]), daemon=True).start()
            active_shop_bots[shop_id] = telebot.TeleBot(token)
        else:
            bot.send_message(
                message.chat.id,
                "❌ Неверный токен",
                reply_markup=keyboards.create_shop_management_menu(shop_id))
    
    elif user_state == UserState.CREATING_CATEGORY:
        category_name = message.text.strip()
        shop_id = user_states.get(f"{user_id}_shop_id")
        
        if category_name.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            bot.send_message(
                message.chat.id,
                "❌ Создание раздела отменено",
                reply_markup=keyboards.create_shop_management_menu(shop_id))
            return
            
        if len(category_name) < 2:
            bot.send_message(message.chat.id, "❌ Название раздела должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        category_id = database.create_category(shop_id, category_name)
        if not category_id:
            bot.send_message(message.chat.id, "❌ Ошибка при создании раздела")
            return
            
        bot.send_message(
            message.chat.id,
            f"✅ Раздел '{category_name}' создан!",
            reply_markup=keyboards.create_categories_menu(shop_id))
        user_states[user_id] = UserState.SHOP_MENU
    
    elif user_state == UserState.EDITING_CATEGORY_NAME:
        new_name = message.text.strip()
        category_id = user_states.get(f"{user_id}_category_id")
        shop_id = database.get_shop_id_by_category(category_id)
        
        if new_name.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            bot.send_message(
                message.chat.id,
                "❌ Изменение названия раздела отменено",
                reply_markup=keyboards.create_categories_menu(shop_id))
            return
            
        if len(new_name) < 2:
            bot.send_message(message.chat.id, "❌ Название раздела должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
            
        if database.update_category_name(category_id, new_name):
            bot.send_message(
                message.chat.id,
                f"✅ Название раздела изменено на '{new_name}'!",
                reply_markup=keyboards.create_categories_menu(shop_id))
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
                reply_markup=keyboards.create_products_menu(category_id))
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
            user_states[user_id] = UserState.PRODUCT_TYPE
            
            markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
            markup.add("Цифровой", "Физический")
            bot.send_message(message.chat.id, "Выберите тип товара:", reply_markup=markup)
        except:
            bot.send_message(message.chat.id, "❌ Некорректная цена. Введите положительное число")
            return
    
    elif user_state == UserState.PRODUCT_TYPE:
        product_type = message.text.strip().lower()
        if product_type not in ['цифровой', 'физический']:
            bot.send_message(message.chat.id, "Пожалуйста, выберите тип товара, используя кнопки.")
            return
            
        is_digital = (product_type == 'цифровой')
        user_states[f"{user_id}_product_is_digital"] = is_digital
        user_states[user_id] = UserState.PRODUCT_DESCRIPTION
        
        remove_keyboard = types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, 
                        "Введите описание товара (или '-' чтобы пропустить):",
                        reply_markup=remove_keyboard)
    
    elif user_state == UserState.PRODUCT_DESCRIPTION:
        description = message.text.strip()
        if description == '-':
            description = None
            
        user_states[f"{user_id}_product_description"] = description
        user_states[user_id] = UserState.PRODUCT_IMAGE
        
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
                reply_markup=keyboards.create_products_menu(category_id, page))
            user_states[user_id] = UserState.SHOP_MENU
            return
        
        if edit_type == "name":
            new_name = message.text.strip()
            if len(new_name) < 2:
                bot.send_message(message.chat.id, "❌ Название товара должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
                return
            database.update_product(product_id, name=new_name)
            bot.send_message(
                message.chat.id,
                "✅ Название товара обновлено!"
            )
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        elif edit_type == "price":
            try:
                new_price = float(message.text.strip())
                if new_price <= 0:
                    raise ValueError()
                database.update_product(product_id, price=new_price)
                bot.send_message(
                    message.chat.id,
                    "✅ Цена товара обновлена!"
                )
                bot.send_message(
                    message.chat.id,
                    "📦 Товары в разделе:",
                    reply_markup=keyboards.create_products_menu(category_id, page))
            except:
                bot.send_message(message.chat.id, "❌ Некорректная цена. Введите положительное число или 'назад' для отмены")
                return
        
        elif edit_type == "desc":
            new_description = message.text.strip()
            database.update_product(product_id, description=new_description)
            bot.send_message(
                message.chat.id,
                "✅ Описание товара обновлено!"
            )
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        elif edit_type == "photo":
            text = message.text.strip().lower()
            if text == 'пропустить':
                bot.send_message(
                    message.chat.id,
                    "✅ Изображение оставлено без изменений!"
                )
            elif text == 'стандартное':
                database.update_product(product_id, image_path="work_photos/default_not_image.jpg")
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
                reply_markup=keyboards.create_products_menu(category_id, page))

        elif edit_type == "sale":
            text = message.text.strip().lower()
            if text in ("-", "убрать", "нет", "0"):
                database.set_product_sale_price(product_id, None)
                bot.send_message(message.chat.id, "✅ Акционная цена убрана!")
            else:
                try:
                    sale_price = float(text)
                    if sale_price <= 0:
                        raise ValueError()
                    product = database.get_product_info(product_id)
                    if product and sale_price >= product[4]:
                        bot.send_message(message.chat.id, f"❌ Акционная цена должна быть меньше обычной ({product[4]}₽). Попробуйте снова или 'назад':")
                        return
                    database.set_product_sale_price(product_id, sale_price)
                    bot.send_message(message.chat.id, f"✅ Акционная цена {sale_price}₽ установлена!")
                except ValueError:
                    bot.send_message(message.chat.id, "❌ Введите число (цену) или '-' чтобы убрать скидку:")
                    return
            bot.send_message(
                message.chat.id,
                "📦 Товары в разделе:",
                reply_markup=keyboards.create_products_menu(category_id, page))
        
        user_states[user_id] = UserState.SHOP_MENU
    else:
        bot.send_message(
            message.chat.id,
            "Используйте кнопки меню для навигации:",
            reply_markup=keyboards.create_main_menu())

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
        
    if not os.path.exists("product_images"):
        os.makedirs("product_images")

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    image_path = f"product_images/{uuid.uuid4().hex}.jpg"
    with open(image_path, 'wb') as new_file:
        new_file.write(downloaded_file)
    
    category_id = user_states.get(f"{user_id}_category_id")
    product_name = user_states.get(f"{user_id}_product_name")
    product_price = user_states.get(f"{user_id}_product_price")
    description = user_states.get(f"{user_id}_product_description")
    is_digital = user_states.get(f"{user_id}_product_is_digital", True)
    
    
    product_id = database.add_product(category_id, product_name, product_price, image_path, is_digital, description)
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
        reply_markup=keyboards.create_products_menu(category_id))
    
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
    is_digital = user_states.get(f"{user_id}_product_is_digital", True)
    
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
        image_path = "work_photos/default_not_image.jpg"
    else:
        bot.send_message(message.chat.id, "❌ Некорректная опция. Отправьте фото, 'Пропустить', 'Стандартное' или 'назад'")
        return
        
    product_id = database.add_product(category_id, product_name, product_price, image_path, is_digital, description)
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
        reply_markup=keyboards.create_products_menu(category_id))
    
    user_states[user_id] = UserState.SHOP_MENU
    for key in [f"{user_id}_category_id", f"{user_id}_product_name", 
                f"{user_id}_product_price", f"{user_id}_product_description"]:
        if key in user_states:
            del user_states[key]

@bot.message_handler(content_types=['photo'], 
                    func=lambda message: user_states.get(message.from_user.id) == UserState.EDITING_PRODUCT and 
                                       user_states.get(f"{message.from_user.id}_edit_type") == 'photo')
def handle_edit_product_photo(message):
    user_id = message.from_user.id
    product_id = user_states.get(f"{user_id}_product_id")
    category_id = user_states.get(f"{user_id}_category_id")
    page = user_states.get(f"{user_id}_page", 0)
    
    product = database.get_product_info(product_id)
    if not product:
        bot.send_message(message.chat.id, "❌ Товар не найден")
        return
    
    old_image_path = product[5]
    
    if not os.path.exists("product_images"):
        os.makedirs("product_images")

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    new_image_path = f"product_images/{uuid.uuid4().hex}.jpg"
    with open(new_image_path, 'wb') as new_file:
        new_file.write(downloaded_file)
    
    database.update_product(product_id, image_path=new_image_path)
    
    if old_image_path and os.path.exists(old_image_path) and "default_not_image" not in old_image_path:
        try:
            os.remove(old_image_path)
        except Exception as e:
            logging.error(f"Ошибка при удалении старого изображения: {e}")

    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass

    bot.send_message(
        message.chat.id,
        "✅ Фото товара обновлено!"
    )
    bot.send_message(
        message.chat.id,
        "📦 Товары в разделе:",
        reply_markup=keyboards.create_products_menu(category_id, page)
    )
    user_states[user_id] = UserState.SHOP_MENU


if __name__ == "__main__":
    print("Инициализация базы данных...")
    database.init_database()
    print("База данных готова!")
    
    print(f"Бот-менеджер запущен! Токен: {config.BOT_TOKEN}")
    
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, bot_token, welcome_message FROM shops WHERE bot_token IS NOT NULL AND is_running = 1")
    shops = cursor.fetchall()
    conn.close()
    
    for shop_id, bot_token, welcome_message in shops:
        threading.Thread(target=shop_bot.run_shop_bot, args=(shop_id, bot_token, welcome_message), daemon=True).start()
        active_shop_bots[shop_id] = telebot.TeleBot(bot_token)
    
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")