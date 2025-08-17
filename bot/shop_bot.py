import os
import sqlite3
import telebot
import logging

import database as database
import keyboards as keyboards
from states import ShopBotState


def run_shop_bot(shop_id, bot_token, welcome_message):
    shop_bot = telebot.TeleBot(bot_token)
    shop_bot_states = {}
    
    def create_shop_main_menu():
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        btn_products = telebot.types.InlineKeyboardButton("📦 Товары", callback_data="shop_products")
        btn_cart = telebot.types.InlineKeyboardButton("🛒 Корзина", callback_data="view_cart")
        btn_reviews = telebot.types.InlineKeyboardButton("📊 Отзывы", callback_data="shop_reviews")
        btn_search = telebot.types.InlineKeyboardButton("🔍 Поиск", callback_data="shop_search")
        btn_recs = telebot.types.InlineKeyboardButton("✨ Похожие магазины", callback_data="shop_recommendations")
        markup.add(btn_products, btn_cart)
        markup.add(btn_reviews, btn_search)
        markup.add(btn_recs)
        return markup

    def show_products_list(call, products, title="Товары", back_data="shop_main_menu"):
        if not products:
            shop_bot.edit_message_text("Нет товаров.", call.message.chat.id, call.message.message_id, reply_markup=create_shop_main_menu())
            return
        text = title + "\n\n"
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        for product in products:
            product_id = product[0]
            name = product[2] if len(product) > 5 else product[1]
            price = product[4] if len(product) > 5 else product[2]
            text += f"{name} (ID: {product_id}) - {price}₽\n"
            markup.add(telebot.types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))
        markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data=back_data))
        shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_product_detail(call, product_id):
        product = database.get_product_info(product_id)
        if not product:
            shop_bot.answer_callback_query(call.id, "Товар не найден")
            return
        
        prod_id = product[0]
        name = product[2]
        description = product[3]
        price = product[4]
        image_path = product[5]
        
        caption = f"📱 Артикул: {prod_id}\n📦 {name}\n💰 {price} RUB\n📝 {description or 'Нет описания'}"
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add_to_cart_{prod_id}"))
        markup.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_list"))
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
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(telebot.types.InlineKeyboardButton("Установить мин. цену", callback_data="set_min_price"))
        markup.add(telebot.types.InlineKeyboardButton("Установить макс. цену", callback_data="set_max_price"))
        markup.add(telebot.types.InlineKeyboardButton("Выбрать категорию", callback_data="choose_category"))
        markup.add(telebot.types.InlineKeyboardButton("Сорт. по цене ↑", callback_data="filter_sort_price_asc"))
        markup.add(telebot.types.InlineKeyboardButton("Сорт. по цене ↓", callback_data="filter_sort_price_desc"))
        markup.add(telebot.types.InlineKeyboardButton("Сорт. по популярности", callback_data="filter_sort_popularity"))
        markup.add(telebot.types.InlineKeyboardButton("Сорт. по новизне", callback_data="filter_sort_newest"))
        markup.add(telebot.types.InlineKeyboardButton("Применить", callback_data="apply_filters"))
        markup.add(telebot.types.InlineKeyboardButton("Сбросить", callback_data="reset_filters"))
        markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_search"))
        return text, markup

    def get_category_name(category_id):
        if not category_id:
            return None
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
        name = cursor.fetchone()
        conn.close()
        return name[0] if name else None

    def handle_delivery_address(message, product_id, shop_id, customer_id):
        delivery_address = message.text.strip()
        if not delivery_address:
            shop_bot.send_message(message.chat.id, "❌ Адрес не может быть пустым")
            return
            
        shop_info = database.get_shop_info(shop_id)
        if not shop_info:
            return
            
        payment_method = shop_info[4]
        
        items = database.get_cart_items(shop_id, customer_id)
        if not items:
            shop_bot.send_message(message.chat.id, "🛒 Ваша корзина пуста")
            return
        
        conn = sqlite3.connect(database.DB_NAME)
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
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shop_admins WHERE shop_id = ?", (shop_id,))
        admin_ids.extend([row[0] for row in cursor.fetchall()])
        conn.close()
        
        for admin_id in admin_ids:
            try:
                import bot.main as main
                main.bot.send_message(
                    admin_id,
                    f"🆕 Новый заказ!\n\nМагазин: {shop_info[2]}\n{order_details}💰 Итог: {total_price}₽\n🏠 Адрес: {delivery_address}\n👤 Покупатель: @{message.from_user.username or 'Не указан'}\n💳 Способ оплаты: {payment_method}"
                )
            except:
                pass
        
        if payment_method == 'online':
            payment_url = database.create_payment_link(total_price, order_ids[0], shop_id)
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
        
        database.clear_cart(shop_id, customer_id)
        shop_bot_states[customer_id] = ShopBotState.MAIN_MENU

    @shop_bot.message_handler(commands=['start'])
    def shop_start_handler(message):
        database.add_user(message.from_user.id, message.from_user.username)
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
                items = database.get_cart_items(shop_id, user_id)
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
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name} x{quantity} - {item_total}₽\n"
                    markup.add(telebot.types.InlineKeyboardButton(f"❌ Удалить {name}", callback_data=f"remove_from_cart_{product_id}"))

                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.add(
                    telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    cart_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data.startswith("add_to_cart_"):
                product_id = int(data.split("_")[-1])
                if database.add_to_cart(shop_id, user_id, product_id):
                    shop_bot.answer_callback_query(call.id, "Товар добавлен в корзину!")
                else:
                    shop_bot.answer_callback_query(call.id, "❌ Ошибка при добавлении в корзину")

            elif data.startswith("remove_from_cart_"):
                product_id = int(data.split("_")[-1])
                database.remove_from_cart(shop_id, user_id, product_id)
                shop_bot.answer_callback_query(call.id, "Товар удален из корзины")
                items = database.get_cart_items(shop_id, user_id)
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
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name} x{quantity} - {item_total}₽\n"
                    markup.add(telebot.types.InlineKeyboardButton(f"❌ Удалить {name}", callback_data=f"remove_from_cart_{product_id}"))

                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.add(
                    telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    cart_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data == "clear_cart":
                database.clear_cart(shop_id, user_id)
                shop_bot.edit_message_text(
                    "🛒 Корзина очищена",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            elif data == "order_cart":
                items = database.get_cart_items(shop_id, user_id)
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
                    reply_markup=keyboards.create_back_button_menu("view_cart")
                )

            elif data == "shop_products":
                categories = database.get_shop_categories(shop_id)
                if not categories:
                    shop_bot.edit_message_text("В магазине нет товаров.", call.message.chat.id, call.message.message_id, reply_markup=create_shop_main_menu())
                    return
                if len(categories) == 1:
                    products = database.get_category_products(categories[0][0])
                    shop_bot_states[f"{user_id}_current_list"] = products
                    shop_bot_states[f"{user_id}_current_title"] = "Товары"
                    shop_bot_states[f"{user_id}_current_back"] = "shop_main_menu"
                    show_products_list(call, products, "Товары", "shop_main_menu")
                else:
                    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                    for cat_id, name in categories:
                        markup.add(telebot.types.InlineKeyboardButton(name, callback_data=f"shop_category_{cat_id}"))
                    markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                    shop_bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "shop_main_menu":
                shop_bot_states[user_id] = ShopBotState.MAIN_MENU
                shop_info = database.get_shop_info(shop_id)
                shop_bot.edit_message_text(
                    shop_info[5],
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            elif data == "shop_reviews":
                conn = sqlite3.connect(database.DB_NAME)
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

                markup = telebot.types.InlineKeyboardMarkup()
                markup.add(telebot.types.InlineKeyboardButton("💬 Оставить отзыв", callback_data="shop_leave_review"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    review_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            elif data == "shop_leave_review":
                conn = sqlite3.connect(database.DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM reviews WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
                if cursor.fetchone():
                    shop_bot.answer_callback_query(call.id, "Вы уже оставили отзыв для этого магазина")
                    conn.close()
                    return
                conn.close()

                shop_bot_states[user_id] = ShopBotState.REVIEW_RATING
                markup = telebot.types.InlineKeyboardMarkup(row_width=5)
                for i in range(1, 6):
                    markup.add(telebot.types.InlineKeyboardButton(f"{i}⭐", callback_data=f"shop_rating_{i}"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_reviews"))
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
                    reply_markup=keyboards.create_back_button_menu("shop_leave_review")
                )

            elif data.startswith("shop_category_"):
                category_id = int(data.split("_")[-1])
                products = database.get_category_products(category_id)
                shop_bot_states[f"{user_id}_current_list"] = products
                shop_bot_states[f"{user_id}_current_title"] = "Товары в категории"
                shop_bot_states[f"{user_id}_current_back"] = "shop_products"
                show_products_list(call, products, "Товары в категории", "shop_products")

            elif data.startswith("view_product_"):
                product_id = int(data.split("_")[-1])
                show_product_detail(call, product_id)

            elif data == "back_to_list":
                try:
                    shop_bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass

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
                markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                for product in products:
                    product_id = product[0]
                    name = product[2] if len(product) > 5 else product[1]
                    price = product[4] if len(product) > 5 else product[2]
                    text += f"{name} (ID: {product_id}) - {price}₽\n"
                    markup.add(telebot.types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data=back_data))

                shop_bot.send_message(
                    call.message.chat.id,
                    text,
                    reply_markup=markup
                )
        
            elif data == "shop_search":
                shop_bot_states[user_id] = ShopBotState.SEARCH_MODE
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(telebot.types.InlineKeyboardButton("По имени", callback_data="search_type_name"))
                markup.add(telebot.types.InlineKeyboardButton("По артикулу", callback_data="search_type_id"))
                markup.add(telebot.types.InlineKeyboardButton("Фильтры", callback_data="search_filters"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text("🔍 Поиск товаров:\nВыберите тип:", call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data.startswith("search_type_"):
                type_ = data.split("_")[-1]
                shop_bot_states[f"{user_id}_search_type"] = type_
                shop_bot_states[user_id] = ShopBotState.SEARCH_INPUT
                bot_text = "Введите артикул:" if type_ == 'id' else "Введите имя:"
                shop_bot.edit_message_text(bot_text, call.message.chat.id, call.message.message_id, reply_markup=keyboards.create_back_button_menu("shop_search"))

            elif data == "search_filters":
                shop_bot_states[user_id] = ShopBotState.FILTER_MODE
                if f"{user_id}_filters" not in shop_bot_states:
                    shop_bot_states[f"{user_id}_filters"] = {'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'}
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "set_min_price":
                shop_bot_states[user_id] = ShopBotState.FILTER_MIN_PRICE
                shop_bot.edit_message_text("Введите минимальную цену:", call.message.chat.id, call.message.message_id, reply_markup=keyboards.create_back_button_menu("search_filters"))

            elif data == "set_max_price":
                shop_bot_states[user_id] = ShopBotState.FILTER_MAX_PRICE
                shop_bot.edit_message_text("Введите максимальную цену:", call.message.chat.id, call.message.message_id, reply_markup=keyboards.create_back_button_menu("search_filters"))

            elif data == "choose_category":
                categories = database.get_shop_categories(shop_id)
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                for cat_id, name in categories:
                    markup.add(telebot.types.InlineKeyboardButton(name, callback_data=f"filter_category_{cat_id}"))
                markup.add(telebot.types.InlineKeyboardButton("Все категории", callback_data="filter_category_none"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="search_filters"))
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
                results = database.search_products(shop_id, None, 'name', filters['price_min'], filters['price_max'], filters['category_id'], filters['sort_by'])
                shop_bot_states[f"{user_id}_current_list"] = results
                shop_bot_states[f"{user_id}_current_title"] = "Результаты поиска"
                shop_bot_states[f"{user_id}_current_back"] = "search_filters"
                show_products_list(call, results, "Результаты поиска", "search_filters")

            elif data == "shop_recommendations":
                shops = database.get_similar_shops(shop_id)
                text = "✨ Похожие магазины:\n\n"
                markup = telebot.types.InlineKeyboardMarkup()
                for s_id, name, username, rating in shops:
                    stars = "⭐" * int(rating)
                    text += f"{name} {stars} ({rating:.1f})\n"
                    if username:
                        markup.add(telebot.types.InlineKeyboardButton("Посетить магазин", url=f"https://t.me/{username}"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
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
                reply_markup=keyboards.create_back_button_menu("view_cart")
            )
            return
        handle_delivery_address(message, None, shop_id, message.from_user.id)

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.REVIEW_TEXT)
    def handle_review_text(message):
        user_id = message.from_user.id
        if message.text.strip().lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.REVIEW_RATING
            markup = telebot.types.InlineKeyboardMarkup(row_width=5)
            for i in range(1, 6):
                markup.add(telebot.types.InlineKeyboardButton(f"{i}⭐", callback_data=f"shop_rating_{i}"))
            markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_reviews"))
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
        
        conn = sqlite3.connect(database.DB_NAME)
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
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(telebot.types.InlineKeyboardButton("По имени", callback_data="search_type_name"))
            markup.add(telebot.types.InlineKeyboardButton("По артикулу", callback_data="search_type_id"))
            markup.add(telebot.types.InlineKeyboardButton("Фильтры", callback_data="search_filters"))
            markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
            shop_bot.send_message(message.chat.id, "🔍 Поиск товаров:\nВыберите тип:", reply_markup=markup)
            return
        type_ = shop_bot_states.get(f"{message.from_user.id}_search_type")
        if type_ == 'id' and not query.isdigit():
            shop_bot.send_message(message.chat.id, "Артикул должен быть числом. Попробуйте снова.")
            return
        results = database.search_products(shop_id, query, type_)
        shop_bot_states[f"{message.from_user.id}_current_list"] = results
        shop_bot_states[f"{message.from_user.id}_current_title"] = "Результаты поиска"
        shop_bot_states[f"{message.from_user.id}_current_back"] = "shop_search"
        text = "Результаты поиска:\n\n"
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        for product in results:
            product_id, _, name, _, price, _ = product[:6]
            text += f"{name} (ID: {product_id}) - {price}₽\n"
            markup.add(telebot.types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))
        markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_search"))
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