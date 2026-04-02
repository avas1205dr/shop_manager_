import os
import sqlite3
import telebot
import logging

from telebot import types

import database as database
import keyboards as keyboards
from states import ShopBotState


PRODUCTS_PER_PAGE = 5

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
    
    def show_products_list(call, products, title="Товары", back_data="shop_main_menu", page=0):
        if not products:
            shop_bot.edit_message_text("Нет товаров.", call.message.chat.id, call.message.message_id, reply_markup=create_shop_main_menu())
            return
        
        total_pages = (len(products) + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE
        start_idx = page * PRODUCTS_PER_PAGE
        end_idx = start_idx + PRODUCTS_PER_PAGE
        page_products = products[start_idx:end_idx]

        text = f"{title} (Страница {page+1}/{total_pages})\n\n"
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        
        for product in page_products:
            product_id = product[0]
            name = product[2] if len(product) > 5 else product[1]
            price = product[4] if len(product) > 5 else product[2]
            text += f"{name} (ID: {product_id}) - {price}₽\n"
            markup.add(telebot.types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{product_id}"))

        # Кнопки пагинации
        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data=f"products_page_{page-1}"))
        if page < total_pages - 1:
            pagination_buttons.append(telebot.types.InlineKeyboardButton("➡️ Вперед", callback_data=f"products_page_{page+1}"))
        
        if pagination_buttons:
            markup.row(*pagination_buttons)
        
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

        display_price, orig_price, has_sale = database.get_product_display_price(product)
        if has_sale:
            price_line = f"~~{orig_price}₽~~ 🔥 {display_price}₽"
        else:
            price_line = f"{display_price}₽"

        caption = f"📱 Артикул: {prod_id}\n📦 {name}\n💰 {price_line}\n📝 {description or 'Нет описания'}"
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("👛 Заказать", callback_data=f"buy_product_{prod_id}"))
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

        # Применяем промокод если есть
        promo = shop_bot_states.get(f"{customer_id}_promo")
        original_price = total_price
        if promo:
            if promo['discount_type'] == 'percent':
                total_price = round(total_price * (1 - promo['discount_value'] / 100), 2)
            else:
                total_price = max(0, round(total_price - promo['discount_value'], 2))
            saved = round(original_price - total_price, 2)
            order_details += f"\n🎟️ Промокод {promo['code']}: скидка {saved:.0f}₽"
            database.use_promocode(promo['id'])
            shop_bot_states.pop(f"{customer_id}_promo", None)

        for product_id, name, price, quantity in items:
            cursor.execute("INSERT INTO orders (shop_id, customer_user_id, product_id, total_price, delivery_address) VALUES (?, ?, ?, ?, ?)",
                         (shop_id, customer_id, product_id, total_price, delivery_address))
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

    def show_reviews_page(chat_id, message_id, page=0):
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT AVG(rating), COUNT(*) FROM reviews WHERE shop_id = ?", (shop_id,))
        stats = cursor.fetchone()
        conn.close()
        
        avg_rating = float(stats[0] or 0)
        total_reviews_count = stats[1] or 0

        reviews_list, _ = database.get_shop_reviews(shop_id, page=page)
        
        rating_stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
        text = f"📊 Отзывы о магазине\n"
        text += f"Рейтинг: {rating_stars} ({avg_rating:.1f}/5)\n"
        text += f"Всего отзывов: {total_reviews_count}\n\n"
        
        if not reviews_list:
            text += "Отзывов пока нет. Будьте первыми!"
        else:
            for username, rating, review_text, date in reviews_list:
                user_display = f"@{username}" if username else "Аноним"
                stars = "⭐" * rating
                clean_date = date.split(' ')[0] if date else ""
                content = review_text if review_text else "Без текста"
                text += f"👤 {user_display} ({clean_date})\n{stars}\n💬 {content}\n\n"
                
        markup = keyboards.create_shop_reviews_pagination(page, total_reviews_count)
        
        shop_bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
    
    @shop_bot.message_handler(commands=['start'])
    def shop_start_handler(message):
        database.add_user(message.from_user.id, message.from_user.username)
        database.register_shop_user(shop_id, message.from_user.id)

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
                markup = telebot.types.InlineKeyboardMarkup(row_width=4)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name}\n   Цена: {price}₽ x {quantity} = {item_total}₽\n"
                    markup.row(
                        telebot.types.InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_from_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton("➖", callback_data=f"decrease_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton(f"{quantity}", callback_data=f"change_quantity_{product_id}"),
                        telebot.types.InlineKeyboardButton("➕", callback_data=f"increase_cart_{product_id}")
                    )

                cart_text += f"\n💰 Итог: {total_price}₽"
                # Показываем применённый промокод
                promo = shop_bot_states.get(f"{user_id}_promo")
                if promo:
                    disc = f"-{int(promo['discount_value'])}%" if promo['discount_type'] == 'percent' else f"-{int(promo['discount_value'])}₽"
                    if promo['discount_type'] == 'percent':
                        discounted = total_price * (1 - promo['discount_value'] / 100)
                    else:
                        discounted = max(0, total_price - promo['discount_value'])
                    cart_text += "\n🎟️ Промокод <b>" + promo['code'] + "</b>: " + disc + "\n"
                    markup.row(telebot.types.InlineKeyboardButton("❌ Убрать промокод", callback_data="remove_promo"))
                else:
                    markup.row(telebot.types.InlineKeyboardButton("🎟️ Ввести промокод", callback_data="enter_promo"))
                markup.row(
                    telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.row(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                shop_bot.edit_message_text(
                    cart_text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup,
                    parse_mode="HTML"
                )

            elif data.startswith("increase_cart_"):
                product_id = int(data.split("_")[-1])
                database.update_cart_quantity(shop_id, user_id, product_id, 1)
                # Обновляем сообщение корзины
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
                markup = telebot.types.InlineKeyboardMarkup(row_width=4)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name}\n   Цена: {price}₽ x {quantity} = {item_total}₽\n"
                    markup.row(
                        telebot.types.InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_from_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton("➖", callback_data=f"decrease_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton(f"{quantity}", callback_data=f"change_quantity_{product_id}"),
                        telebot.types.InlineKeyboardButton("➕", callback_data=f"increase_cart_{product_id}")
                    )

                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.row(
                    telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.row(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                try:
                    shop_bot.edit_message_text(
                        cart_text,
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=markup
                    )
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" in str(e):
                        pass
                    else:
                        raise e
                
                
            elif data.startswith("decrease_cart_"):
                product_id = int(data.split("_")[-1])
                current_quantity = database.get_cart_quantity(shop_id, user_id, product_id)
                if current_quantity > 1:
                    database.update_cart_quantity(shop_id, user_id, product_id, -1)
                else:
                    database.remove_from_cart(shop_id, user_id, product_id)
                
                # Обновляем сообщение корзины
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
                markup = telebot.types.InlineKeyboardMarkup(row_width=4)

                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name}\n   Цена: {price}₽ x {quantity} = {item_total}₽\n"
                    markup.row(
                        telebot.types.InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_from_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton("➖", callback_data=f"decrease_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton(f"{quantity}", callback_data=f"change_quantity_{product_id}"),
                        telebot.types.InlineKeyboardButton("➕", callback_data=f"increase_cart_{product_id}")
                    )

                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.row(
                    telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.row(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))

                try:
                    shop_bot.edit_message_text(
                        cart_text,
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=markup
                    )
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" in str(e):
                        pass
                    else:
                        raise e
    
            elif data.startswith("buy_product_"):
                product_id = int(data.split("_")[-1])
                product = database.get_product_info(product_id)
                if not product:
                    shop_bot.answer_callback_query(call.id, "Товар не найден")
                    return
            
                shop_bot_states[user_id] = ShopBotState.ENTERING_QUANTITY
                shop_bot_states[f"{user_id}_product_id"] = product_id
                
                shop_bot.send_message(
                    call.message.chat.id,
                    "Введите количество товара:",
                    reply_markup=keyboards.create_back_button_menu(f"view_product_{product_id}")
                )
                shop_bot.answer_callback_query(call.id)
                
            
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
            
            elif data.startswith("products_page_"):
                page = int(data.split("_")[-1])
                products = shop_bot_states.get(f"{user_id}_current_list", [])
                title = shop_bot_states.get(f"{user_id}_current_title", "Товары")
                back_data = shop_bot_states.get(f"{user_id}_current_back", "shop_main_menu")
                show_products_list(call, products, title, back_data, page)
            
            elif data == "shop_main_menu":
                shop_bot_states[user_id] = ShopBotState.MAIN_MENU
                shop_info = database.get_shop_info(shop_id)
                shop_bot.edit_message_text(
                    shop_info[5],
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            if data == "shop_reviews":
                shop_bot_states[user_id] = ShopBotState.BROWSING_REVIEWS
                show_reviews_page(call.message.chat.id, call.message.message_id, 0)
                
            elif data.startswith("reviews_next_"):
                page = int(data.split("_")[-1])
                show_reviews_page(call.message.chat.id, call.message.message_id, page)
                
            elif data.startswith("reviews_prev_"):
                page = int(data.split("_")[-1])
                show_reviews_page(call.message.chat.id, call.message.message_id, page)

            elif data == "shop_recommendations":
                # Используем новую улучшенную функцию рекомендаций
                shops = database.get_similar_shops(shop_id)
                text = "✨ Рекомендуемые магазины\n(подобранные специально для вас):\n\n"
                markup = telebot.types.InlineKeyboardMarkup()
                
                if not shops:
                    text += "К сожалению, пока нет рекомендаций."
                
                for s_id, name, username, rating, score, price_diff in shops:
                    stars = "⭐" * int(rating)
                    # Можно добавить инфо, почему рекомендовано
                    reason = ""
                    if score > 5: reason = "🔥 (Похожий выбор)"
                    elif score > 2: reason = "✅ (Схожие товары)"
                    
                    text += f"{name} {stars} {rating:.1f} {reason}\n"
                    if username:
                        markup.add(telebot.types.InlineKeyboardButton(f"Перейти в {name}", url=f"https://t.me/{username}"))
                
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

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

            elif data == "enter_promo":
                shop_bot_states[user_id] = ShopBotState.ENTERING_PROMOCODE
                shop_bot.edit_message_text(
                    "🎟️ Введите промокод:",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("view_cart")
                )

            elif data == "remove_promo":
                shop_bot_states.pop(f"{user_id}_promo", None)
                shop_bot.answer_callback_query(call.id, "Промокод убран")
                # Refresh cart
                items = database.get_cart_items(shop_id, user_id)
                if not items:
                    shop_bot.edit_message_text("🛒 Ваша корзина пуста", call.message.chat.id, call.message.message_id, reply_markup=create_shop_main_menu())
                    return
                total_price = 0
                cart_text = "🛒 Ваша корзина:\n\n"
                markup = telebot.types.InlineKeyboardMarkup(row_width=4)
                for product_id, name, price, quantity in items:
                    item_total = price * quantity
                    total_price += item_total
                    cart_text += f"📦 {name}\n   Цена: {price}₽ x {quantity} = {item_total}₽\n"
                    markup.row(
                        telebot.types.InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_from_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton("➖", callback_data=f"decrease_cart_{product_id}"),
                        telebot.types.InlineKeyboardButton(f"{quantity}", callback_data=f"change_quantity_{product_id}"),
                        telebot.types.InlineKeyboardButton("➕", callback_data=f"increase_cart_{product_id}")
                    )
                cart_text += f"\n💰 Итог: {total_price}₽"
                markup.row(telebot.types.InlineKeyboardButton("🎟️ Ввести промокод", callback_data="enter_promo"))
                markup.row(
                    telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
                    telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")
                )
                markup.row(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text(cart_text, call.message.chat.id, call.message.message_id, reply_markup=markup)

        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 400 and 'message is not modified' in str(e):
                pass
            else:
                logging.error(f"Shop callback error: {str(e)}")
                shop_bot.answer_callback_query(call.id, "Произошла ошибка. Попробуйте снова.")
        except Exception as e:
            logging.error(f"Shop callback error: {str(e)}")
            shop_bot.answer_callback_query(call.id, f"Ошибка: {str(e)[:100]}")

    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.ENTERING_PROMOCODE)
    def handle_promocode_input(message):
        user_id = message.from_user.id
        code = message.text.strip()
        if code.lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.VIEWING_CART
            shop_bot.send_message(message.chat.id, "Отмена ввода промокода.",
                                  reply_markup=keyboards.create_back_button_menu("view_cart"))
            return
        promo = database.validate_promocode(shop_id, code)
        if not promo:
            shop_bot.send_message(message.chat.id,
                "❌ Промокод не найден или недействителен. Попробуйте ещё раз или отправьте 'назад':")
            return
        shop_bot_states[f"{user_id}_promo"] = promo
        shop_bot_states[user_id] = ShopBotState.VIEWING_CART
        disc_str = f"-{int(promo['discount_value'])}%" if promo['discount_type'] == 'percent' else f"-{int(promo['discount_value'])}₽"
        shop_bot.send_message(
            message.chat.id,
            "✅ Промокод <b>" + promo['code'] + "</b> применён! Скидка: " + disc_str + "\n\nВернитесь в корзину для оформления.",
            parse_mode="HTML",
            reply_markup=keyboards.create_back_button_menu("view_cart")
        )

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
        
        username = message.from_user.username or None
        database.add_user(user_id, username)
        
        database.add_review(shop_id, user_id, rating, review_text)
        
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
        
        # Создаем искусственный call объект
        class FakeCall:
            def __init__(self, message):
                self.message = message
                self.from_user = message.from_user
                self.data = "search_results"
        
        fake_call = FakeCall(message)
        show_products_list(fake_call, results, "Результаты поиска", "shop_search")

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
    
    @shop_bot.message_handler(func=lambda message: shop_bot_states.get(message.from_user.id) == ShopBotState.ENTERING_QUANTITY)
    def handle_quantity_input(message):
        user_id = message.from_user.id
        if message.text.strip().lower() == 'назад':
            product_id = shop_bot_states.get(f"{user_id}_product_id")
            shop_bot_states[user_id] = ShopBotState.MAIN_MENU
            shop_bot.send_message(
                message.chat.id,
                "Отмена заказа",
                reply_markup=create_shop_main_menu()
            )
            return

        try:
            quantity = int(message.text)
            if quantity <= 0:
                raise ValueError
            
            product_id = shop_bot_states.get(f"{user_id}_product_id")
            product = database.get_product_info(product_id)
            
            if not product:
                shop_bot.send_message(message.chat.id, "Товар не найден")
                return

            # Создаем инвойс с указанным количеством
            title = product[2]
            description = product[3] or "Покупка товара"
            price = int(float(product[4]) * 100 * quantity)
            payload = f"product_{product_id}_user_{user_id}_quantity_{quantity}"
            payment_token = database.get_paymaster_token_by_shop_id(shop_id)
            
            shop_bot.send_invoice(
                message.chat.id,
                title,
                description,
                payload,
                payment_token,
                'rub',
                [types.LabeledPrice(label=f"{title} x{quantity}", amount=price)],
                start_parameter='product',
            )
            
            shop_bot_states[user_id] = ShopBotState.MAIN_MENU
            
        except ValueError:
            shop_bot.send_message(message.chat.id, "Пожалуйста, введите корректное количество (целое число больше 0):")

    @shop_bot.message_handler(content_types=['successful_payment'])
    def handle_successful_payment(message):
        try:
            payload_parts = message.successful_payment.invoice_payload.split('_')
            product_id = int(payload_parts[1])
            user_id = int(payload_parts[3])
            quantity = int(payload_parts[5])
            total_price = message.successful_payment.total_amount / 100
            
            if database.buy_product(shop_id, user_id, product_id, quantity, total_price, 'Цифровой товар (оплачено)'):
                shop_bot.send_message(message.chat.id, "✅ Товар успешно оплачен и добавлен в ваши покупки!")
            else:
                shop_bot.send_message(message.chat.id, "❌ Ошибка при обработке покупки")
                
        except Exception as e:
            logging.error(f"Ошибка обработки оплаты: {e}")
            shop_bot.send_message(message.chat.id, "❌ Произошла ошибка при обработке платежа")

    @shop_bot.pre_checkout_query_handler(lambda query: True)
    def pre_checkout_query(pre_checkout_q: types.PreCheckoutQuery):
        shop_bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)
        
        
    try:
        shop_bot.infinity_polling()
    except Exception as e:
        logging.error(f"Ошибка в боте магазина {shop_id}: {e}")