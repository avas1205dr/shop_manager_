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

    # ─────────────────── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ───────────────────

    def create_shop_main_menu():
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        btn_products = telebot.types.InlineKeyboardButton("📦 Товары", callback_data="shop_products")
        btn_cart     = telebot.types.InlineKeyboardButton("🛒 Корзина", callback_data="view_cart")
        btn_reviews  = telebot.types.InlineKeyboardButton("📊 Отзывы", callback_data="shop_reviews")
        btn_search   = telebot.types.InlineKeyboardButton("🔍 Поиск", callback_data="shop_search")
        btn_recs     = telebot.types.InlineKeyboardButton("✨ Похожие магазины", callback_data="shop_recommendations")
        markup.add(btn_products, btn_cart)
        markup.add(btn_reviews, btn_search)
        markup.add(btn_recs)
        return markup

    def _apply_promo(total: float, promo: dict) -> float:
        """Применяет промокод и возвращает итоговую цену (минимум 1 коп)."""
        if promo['discount_type'] == 'percent':
            total = round(total * (1 - promo['discount_value'] / 100), 2)
        else:
            total = round(total - promo['discount_value'], 2)
        return max(0.01, total)  # Telegram требует >0

    def _build_cart_message(user_id):
        """Строит текст и разметку корзины. Возвращает (text, markup) или None если пуста."""
        items = database.get_cart_items(shop_id, user_id)
        if not items:
            return None, None

        total_price = 0
        cart_text = "🛒 Ваша корзина:\n\n"
        markup = telebot.types.InlineKeyboardMarkup(row_width=4)

        for product_id, name, price, quantity in items:
            item_total = price * quantity
            total_price += item_total
            cart_text += f"📦 {name}\n   {price}₽ × {quantity} = {item_total}₽\n"
            markup.row(
                telebot.types.InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_from_cart_{product_id}"),
                telebot.types.InlineKeyboardButton("➖", callback_data=f"decrease_cart_{product_id}"),
                telebot.types.InlineKeyboardButton(str(quantity), callback_data=f"change_quantity_{product_id}"),
                telebot.types.InlineKeyboardButton("➕", callback_data=f"increase_cart_{product_id}"),
            )

        cart_text += f"\n💰 Итого: {total_price:.2f}₽"

        promo = shop_bot_states.get(f"{user_id}_promo")
        if promo:
            discounted = _apply_promo(total_price, promo)
            disc_str = (f"-{int(promo['discount_value'])}%"
                        if promo['discount_type'] == 'percent'
                        else f"-{int(promo['discount_value'])}₽")
            saved = round(total_price - discounted, 2)
            cart_text += (f"\n🎟️ Промокод <b>{promo['code']}</b>: {disc_str}"
                          f"\n💸 Итого со скидкой: {discounted:.2f}₽ (экономия {saved:.2f}₽)")
            markup.row(telebot.types.InlineKeyboardButton("❌ Убрать промокод", callback_data="remove_promo"))
        else:
            markup.row(telebot.types.InlineKeyboardButton("🎟️ Ввести промокод", callback_data="enter_promo"))

        markup.row(
            telebot.types.InlineKeyboardButton("✅ Оформить заказ", callback_data="order_cart"),
            telebot.types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart"),
        )
        markup.row(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
        return cart_text, markup

    def show_products_list(call, products, title="Товары", back_data="shop_main_menu", page=0):
        if not products:
            shop_bot.edit_message_text(
                "Нет товаров.", call.message.chat.id, call.message.message_id,
                reply_markup=create_shop_main_menu()
            )
            return

        total_pages = (len(products) + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE
        start_idx = page * PRODUCTS_PER_PAGE
        end_idx = start_idx + PRODUCTS_PER_PAGE
        page_products = products[start_idx:end_idx]

        text = f"{title} (Страница {page+1}/{total_pages})\n\n"
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)

        for product in page_products:
            product_id = product[0]
            name  = product[2] if len(product) > 5 else product[1]
            price = product[4] if len(product) > 5 else product[2]
            text += f"{name} (ID: {product_id}) — {price}₽\n"
            markup.add(telebot.types.InlineKeyboardButton(
                f"Просмотреть {name}", callback_data=f"view_product_{product_id}"
            ))

        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(telebot.types.InlineKeyboardButton(
                "⬅️ Назад", callback_data=f"products_page_{page-1}"
            ))
        if page < total_pages - 1:
            pagination_buttons.append(telebot.types.InlineKeyboardButton(
                "➡️ Вперед", callback_data=f"products_page_{page+1}"
            ))
        if pagination_buttons:
            markup.row(*pagination_buttons)

        markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data=back_data))
        shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_product_detail(call, product_id):
        product = database.get_product_info(product_id)
        if not product:
            shop_bot.answer_callback_query(call.id, "Товар не найден")
            return

        name        = product[2]
        description = product[3]
        image_path  = product[5]

        display_price, orig_price, has_sale = database.get_product_display_price(product)
        if has_sale:
            price_line = f"~~{orig_price}₽~~ 🔥 {display_price}₽"
        else:
            price_line = f"{display_price}₽"

        caption = (
            f"📱 Артикул: {product_id}\n"
            f"📦 {name}\n"
            f"💰 {price_line}\n"
            f"📝 {description or 'Нет описания'}"
        )
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("👛 Заказать", callback_data=f"buy_product_{product_id}"))
        markup.add(telebot.types.InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}"))
        markup.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_list"))

        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as photo:
                shop_bot.send_photo(call.message.chat.id, photo, caption=caption, reply_markup=markup)
        else:
            shop_bot.send_message(call.message.chat.id, caption, reply_markup=markup)

    def create_filter_menu(user_id):
        filters = shop_bot_states.get(
            f"{user_id}_filters",
            {'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'}
        )
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

    # ─────────────────── ОФОРМЛЕНИЕ ЗАКАЗА ИЗ КОРЗИНЫ ───────────────────

    def handle_delivery_address(message, product_id, shop_id, customer_id):
        delivery_address = message.text.strip()
        if not delivery_address:
            shop_bot.send_message(message.chat.id, "❌ Адрес не может быть пустым")
            return

        shop_info = database.get_shop_info(shop_id)
        if not shop_info:
            shop_bot.send_message(message.chat.id, "❌ Магазин не найден")
            return

        payment_method = shop_info[4]

        items = database.get_cart_items(shop_id, customer_id)
        if not items:
            shop_bot.send_message(message.chat.id, "🛒 Ваша корзина пуста")
            return

        total_price = 0
        order_details = ""
        for pid, name, price, quantity in items:
            total_price += price * quantity
            order_details += f"📦 {name} ×{quantity} — {price * quantity}₽\n"

        # Применяем промокод
        promo = shop_bot_states.get(f"{customer_id}_promo")
        if promo:
            discounted = _apply_promo(total_price, promo)
            saved = round(total_price - discounted, 2)
            disc_str = (f"-{int(promo['discount_value'])}%"
                        if promo['discount_type'] == 'percent'
                        else f"-{int(promo['discount_value'])}₽")
            order_details += f"\n🎟️ Промокод {promo['code']} ({disc_str}): скидка {saved:.2f}₽"
            total_price = discounted
            database.use_promocode(promo['id'])
            shop_bot_states.pop(f"{customer_id}_promo", None)

        # Записываем заказы в БД
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        order_ids = []
        for pid, name, price, quantity in items:
            cursor.execute(
                "INSERT INTO orders (shop_id, customer_user_id, product_id, quantity, total_price, delivery_address) VALUES (?, ?, ?, ?, ?, ?)",
                (shop_id, customer_id, pid, quantity, total_price, delivery_address)
            )
            order_ids.append(cursor.lastrowid)
        conn.commit()
        conn.close()

        # Уведомляем администраторов
        admin_ids = [shop_info[1]]
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shop_admins WHERE shop_id = ?", (shop_id,))
        admin_ids.extend([row[0] for row in cursor.fetchall()])
        conn.close()

        notify_text = (
            f"🆕 Новый заказ!\n\nМагазин: {shop_info[2]}\n{order_details}\n"
            f"💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n"
            f"👤 Покупатель: @{message.from_user.username or 'Не указан'}\n"
            f"💳 Способ оплаты: {payment_method}"
        )
        for admin_id in admin_ids:
            try:
                import bot.main as main
                main.bot.send_message(admin_id, notify_text)
            except Exception:
                pass

        if payment_method == 'online':
            payment_url = database.create_payment_link(total_price, order_ids[0], shop_id)
            if payment_url:
                shop_bot.send_message(
                    message.chat.id,
                    f"🛒 Заказ оформлен!\n\n{order_details}\n💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n\nОплатите заказ по ссылке:\n{payment_url}"
                )
            else:
                shop_bot.send_message(
                    message.chat.id,
                    f"🛒 Заказ оформлен!\n\n{order_details}\n💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n\n❌ Ошибка при создании платежной ссылки"
                )
        else:
            shop_bot.send_message(
                message.chat.id,
                f"🛒 Заказ оформлен!\n\n{order_details}\n💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n💳 Оплата при получении"
            )

        database.clear_cart(shop_id, customer_id)
        shop_bot_states[customer_id] = ShopBotState.MAIN_MENU

    # ─────────────────── ПРЯМАЯ ПОКУПКА: инвойс ───────────────────

    def _send_invoice_for_direct_buy(chat_id, user_id, product_id, quantity):
        """Формирует и отправляет Telegram Invoice с учётом промокода."""
        product = database.get_product_info(product_id)
        if not product:
            shop_bot.send_message(chat_id, "❌ Товар не найден")
            return

        title       = product[2]
        description = product[3] or "Покупка товара"
        display_price, _, _ = database.get_product_display_price(product)
        total = float(display_price) * quantity

        promo = shop_bot_states.pop(f"{user_id}_promo_direct", None)
        label_suffix = ""
        if promo:
            discounted = _apply_promo(total, promo)
            saved = round(total - discounted, 2)
            disc_str = (f"-{int(promo['discount_value'])}%"
                        if promo['discount_type'] == 'percent'
                        else f"-{int(promo['discount_value'])}₽")
            label_suffix = f" ({disc_str})"
            total = discounted

            # Если скидка 100% — оформляем как бесплатный заказ без инвойса
            if total < 1.0:
                database.use_promocode(promo['id'])
                database.buy_product(shop_id, user_id, product_id, quantity, 0, 'Цифровой товар (промокод 100%)')
                shop_bot.send_message(
                    chat_id,
                    f"✅ Товар <b>{title}</b> ×{quantity} оформлен бесплатно по промокоду <b>{promo['code']}</b>!",
                    parse_mode="HTML",
                    reply_markup=create_shop_main_menu()
                )
                shop_bot_states[user_id] = ShopBotState.MAIN_MENU
                return

            database.use_promocode(promo['id'])

        payment_token = database.get_paymaster_token_by_shop_id(shop_id)
        if not payment_token:
            shop_bot.send_message(
                chat_id,
                "❌ Оплата не настроена. Обратитесь к администратору магазина.",
                reply_markup=create_shop_main_menu()
            )
            shop_bot_states[user_id] = ShopBotState.MAIN_MENU
            return

        # RUB: сумма в копейках, диапазон 100–9_999_900
        price_kopecks = int(round(total * 100))
        price_kopecks = max(100, min(price_kopecks, 9_999_900))

        payload = f"product_{product_id}_user_{user_id}_quantity_{quantity}"

        shop_bot.send_invoice(
            chat_id,
            title,
            description + label_suffix,
            payload,
            payment_token,
            'rub',
            [types.LabeledPrice(label=f"{title} ×{quantity}{label_suffix}", amount=price_kopecks)],
            start_parameter='product',
        )
        shop_bot_states[user_id] = ShopBotState.MAIN_MENU

    # ─────────────────── ОТЗЫВЫ ───────────────────

    def show_reviews_page(chat_id, message_id, page=0):
        conn = sqlite3.connect(database.DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT AVG(rating), COUNT(*) FROM reviews WHERE shop_id = ?", (shop_id,))
        stats = cursor.fetchone()
        conn.close()

        avg_rating         = float(stats[0] or 0)
        total_reviews_count = stats[1] or 0

        reviews_list, _ = database.get_shop_reviews(shop_id, page=page)

        rating_stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
        text  = f"📊 Отзывы о магазине\n"
        text += f"Рейтинг: {rating_stars} ({avg_rating:.1f}/5)\n"
        text += f"Всего отзывов: {total_reviews_count}\n\n"

        if not reviews_list:
            text += "Отзывов пока нет. Будьте первыми!"
        else:
            for username, rating, review_text, date in reviews_list:
                user_display = f"@{username}" if username else "Аноним"
                stars        = "⭐" * rating
                clean_date   = date.split(' ')[0] if date else ""
                content      = review_text if review_text else "Без текста"
                text += f"👤 {user_display} ({clean_date})\n{stars}\n💬 {content}\n\n"

        markup = keyboards.create_shop_reviews_pagination(page, total_reviews_count)
        shop_bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)

    # ─────────────────── /start ───────────────────

    @shop_bot.message_handler(commands=['start'])
    def shop_start_handler(message):
        database.add_user(message.from_user.id, message.from_user.username)
        database.register_shop_user(shop_id, message.from_user.id)
        shop_bot_states[message.from_user.id] = ShopBotState.MAIN_MENU
        shop_bot.send_message(message.chat.id, welcome_message, reply_markup=create_shop_main_menu())

    # ─────────────────── CALLBACK HANDLER ───────────────────

    @shop_bot.callback_query_handler(func=lambda call: True)
    def shop_callback_handler(call):
        user_id = call.from_user.id
        data    = call.data
        try:
            # ── КОРЗИНА: просмотр ──
            if data == "view_cart":
                shop_bot_states[user_id] = ShopBotState.VIEWING_CART
                cart_text, markup = _build_cart_message(user_id)
                if not cart_text:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                shop_bot.edit_message_text(
                    cart_text, call.message.chat.id, call.message.message_id,
                    reply_markup=markup, parse_mode="HTML"
                )

            # ── КОРЗИНА: увеличить количество ──
            elif data.startswith("increase_cart_"):
                product_id = int(data.split("_")[-1])
                database.update_cart_quantity(shop_id, user_id, product_id, 1)
                cart_text, markup = _build_cart_message(user_id)
                if not cart_text:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                try:
                    shop_bot.edit_message_text(
                        cart_text, call.message.chat.id, call.message.message_id,
                        reply_markup=markup, parse_mode="HTML"
                    )
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" not in str(e):
                        raise

            # ── КОРЗИНА: уменьшить количество ──
            elif data.startswith("decrease_cart_"):
                product_id = int(data.split("_")[-1])
                current_quantity = database.get_cart_quantity(shop_id, user_id, product_id)
                if current_quantity > 1:
                    database.update_cart_quantity(shop_id, user_id, product_id, -1)
                else:
                    database.remove_from_cart(shop_id, user_id, product_id)
                cart_text, markup = _build_cart_message(user_id)
                if not cart_text:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                try:
                    shop_bot.edit_message_text(
                        cart_text, call.message.chat.id, call.message.message_id,
                        reply_markup=markup, parse_mode="HTML"
                    )
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" not in str(e):
                        raise

            # ── ПРЯМАЯ ПОКУПКА: начало ──
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
                    "Введите количество товара:\n\nОтправьте «назад» для отмены",
                    reply_markup=keyboards.create_back_button_menu(f"view_product_{product_id}")
                )
                shop_bot.answer_callback_query(call.id)

            # ── КОРЗИНА: добавить товар ──
            elif data.startswith("add_to_cart_"):
                product_id = int(data.split("_")[-1])
                if database.add_to_cart(shop_id, user_id, product_id):
                    shop_bot.answer_callback_query(call.id, "✅ Товар добавлен в корзину!")
                else:
                    shop_bot.answer_callback_query(call.id, "❌ Ошибка при добавлении в корзину")

            # ── КОРЗИНА: удалить товар ──
            elif data.startswith("remove_from_cart_"):
                product_id = int(data.split("_")[-1])
                database.remove_from_cart(shop_id, user_id, product_id)
                shop_bot.answer_callback_query(call.id, "Товар удалён из корзины")
                cart_text, markup = _build_cart_message(user_id)
                if not cart_text:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                shop_bot.edit_message_text(
                    cart_text, call.message.chat.id, call.message.message_id,
                    reply_markup=markup, parse_mode="HTML"
                )

            # ── КОРЗИНА: очистить ──
            elif data == "clear_cart":
                database.clear_cart(shop_id, user_id)
                shop_bot_states.pop(f"{user_id}_promo", None)
                shop_bot.edit_message_text(
                    "🛒 Корзина очищена",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            # ── КОРЗИНА: оформить заказ ──
            elif data == "order_cart":
                items = database.get_cart_items(shop_id, user_id)
                if not items:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                shop_bot_states[user_id] = ShopBotState.ENTERING_ADDRESS
                shop_bot.edit_message_text(
                    "🏠 Введите адрес доставки:\n\nОтправьте «назад» для отмены",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("view_cart")
                )

            # ── ПРОМОКОД КОРЗИНЫ: ввести ──
            elif data == "enter_promo":
                shop_bot_states[user_id] = ShopBotState.ENTERING_PROMOCODE
                shop_bot.edit_message_text(
                    "🎟️ Введите промокод:\n\nОтправьте «назад» для возврата в корзину",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("view_cart")
                )

            # ── ПРОМОКОД КОРЗИНЫ: убрать ──
            elif data == "remove_promo":
                shop_bot_states.pop(f"{user_id}_promo", None)
                shop_bot.answer_callback_query(call.id, "Промокод убран")
                cart_text, markup = _build_cart_message(user_id)
                if not cart_text:
                    shop_bot.edit_message_text(
                        "🛒 Ваша корзина пуста",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                shop_bot.edit_message_text(
                    cart_text, call.message.chat.id, call.message.message_id,
                    reply_markup=markup, parse_mode="HTML"
                )

            # ── ПРЯМАЯ ПОКУПКА: пропустить промокод ──
            elif data == "skip_promo_direct":
                shop_bot_states.pop(f"{user_id}_promo_direct", None)
                product_id = shop_bot_states.get(f"{user_id}_product_id")
                quantity   = shop_bot_states.get(f"{user_id}_quantity")
                if not product_id or not quantity:
                    shop_bot.answer_callback_query(call.id, "❌ Сессия истекла, начните заново")
                    shop_bot_states[user_id] = ShopBotState.MAIN_MENU
                    return
                shop_bot.answer_callback_query(call.id)
                _send_invoice_for_direct_buy(call.message.chat.id, user_id, product_id, quantity)

            # ── ТОВАРЫ ──
            elif data == "shop_products":
                categories = database.get_shop_categories(shop_id)
                if not categories:
                    shop_bot.edit_message_text(
                        "В магазине нет товаров.",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=create_shop_main_menu()
                    )
                    return
                if len(categories) == 1:
                    products = database.get_category_products(categories[0][0])
                    shop_bot_states[f"{user_id}_current_list"]  = products
                    shop_bot_states[f"{user_id}_current_title"] = "Товары"
                    shop_bot_states[f"{user_id}_current_back"]  = "shop_main_menu"
                    show_products_list(call, products, "Товары", "shop_main_menu")
                else:
                    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                    for cat_id, name in categories:
                        markup.add(telebot.types.InlineKeyboardButton(name, callback_data=f"shop_category_{cat_id}"))
                    markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                    shop_bot.edit_message_text(
                        "Выберите категорию:",
                        call.message.chat.id, call.message.message_id, reply_markup=markup
                    )

            elif data.startswith("products_page_"):
                page      = int(data.split("_")[-1])
                products  = shop_bot_states.get(f"{user_id}_current_list", [])
                title     = shop_bot_states.get(f"{user_id}_current_title", "Товары")
                back_data = shop_bot_states.get(f"{user_id}_current_back", "shop_main_menu")
                show_products_list(call, products, title, back_data, page)

            elif data == "shop_main_menu":
                shop_bot_states[user_id] = ShopBotState.MAIN_MENU
                shop_info = database.get_shop_info(shop_id)
                shop_bot.edit_message_text(
                    shop_info[5] if shop_info else welcome_message,
                    call.message.chat.id, call.message.message_id,
                    reply_markup=create_shop_main_menu()
                )

            # ── ОТЗЫВЫ ──
            elif data == "shop_reviews":
                shop_bot_states[user_id] = ShopBotState.BROWSING_REVIEWS
                show_reviews_page(call.message.chat.id, call.message.message_id, 0)

            elif data.startswith("reviews_next_") or data.startswith("reviews_prev_"):
                page = int(data.split("_")[-1])
                show_reviews_page(call.message.chat.id, call.message.message_id, page)

            elif data == "shop_leave_review":
                conn = sqlite3.connect(database.DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM reviews WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
                already = cursor.fetchone()
                conn.close()
                if already:
                    shop_bot.answer_callback_query(call.id, "Вы уже оставили отзыв для этого магазина")
                    return
                shop_bot_states[user_id] = ShopBotState.REVIEW_RATING
                markup = telebot.types.InlineKeyboardMarkup(row_width=5)
                for i in range(1, 6):
                    markup.add(telebot.types.InlineKeyboardButton(f"{i}⭐", callback_data=f"shop_rating_{i}"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_reviews"))
                shop_bot.edit_message_text(
                    "Оцените магазин от 1 до 5 звёзд:",
                    call.message.chat.id, call.message.message_id, reply_markup=markup
                )

            elif data.startswith("shop_rating_"):
                rating = int(data.split("_")[-1])
                shop_bot_states[user_id]            = ShopBotState.REVIEW_TEXT
                shop_bot_states[f"{user_id}_rating"] = rating
                shop_bot.edit_message_text(
                    f"Вы поставили оценку: {rating}⭐\n\nТеперь напишите отзыв (или /skip чтобы пропустить):\n\nОтправьте «назад» для отмены",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("shop_leave_review")
                )

            # ── КАТЕГОРИИ ──
            elif data.startswith("shop_category_"):
                category_id = int(data.split("_")[-1])
                products    = database.get_category_products(category_id)
                shop_bot_states[f"{user_id}_current_list"]  = products
                shop_bot_states[f"{user_id}_current_title"] = "Товары в категории"
                shop_bot_states[f"{user_id}_current_back"]  = "shop_products"
                show_products_list(call, products, "Товары в категории", "shop_products")

            elif data.startswith("view_product_"):
                product_id = int(data.split("_")[-1])
                show_product_detail(call, product_id)

            elif data == "back_to_list":
                try:
                    shop_bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception:
                    pass
                products  = shop_bot_states.get(f"{user_id}_current_list", [])
                title     = shop_bot_states.get(f"{user_id}_current_title", "Товары")
                back_data = shop_bot_states.get(f"{user_id}_current_back", "shop_main_menu")
                if not products:
                    shop_bot.send_message(
                        call.message.chat.id, "Нет товаров.",
                        reply_markup=create_shop_main_menu()
                    )
                    return
                text   = title + "\n\n"
                markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                for product in products:
                    pid   = product[0]
                    name  = product[2] if len(product) > 5 else product[1]
                    price = product[4] if len(product) > 5 else product[2]
                    text += f"{name} (ID: {pid}) — {price}₽\n"
                    markup.add(telebot.types.InlineKeyboardButton(f"Просмотреть {name}", callback_data=f"view_product_{pid}"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data=back_data))
                shop_bot.send_message(call.message.chat.id, text, reply_markup=markup)

            # ── ПОХОЖИЕ МАГАЗИНЫ ──
            elif data == "shop_recommendations":
                shops = database.get_similar_shops(shop_id)
                text  = "✨ Рекомендуемые магазины:\n\n"
                markup = telebot.types.InlineKeyboardMarkup()
                if not shops:
                    text += "К сожалению, пока нет рекомендаций."
                for s_id, name, username, rating, score, price_diff in shops:
                    stars  = "⭐" * int(rating)
                    reason = ""
                    if score > 5:   reason = "🔥 (Похожий выбор)"
                    elif score > 2: reason = "✅ (Схожие товары)"
                    text += f"{name} {stars} {rating:.1f} {reason}\n"
                    if username:
                        markup.add(telebot.types.InlineKeyboardButton(f"Перейти в {name}", url=f"https://t.me/{username}"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            # ── ПОИСК ──
            elif data == "shop_search":
                shop_bot_states[user_id] = ShopBotState.SEARCH_MODE
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(telebot.types.InlineKeyboardButton("По имени", callback_data="search_type_name"))
                markup.add(telebot.types.InlineKeyboardButton("По артикулу", callback_data="search_type_id"))
                markup.add(telebot.types.InlineKeyboardButton("Фильтры", callback_data="search_filters"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
                shop_bot.edit_message_text(
                    "🔍 Поиск товаров:\nВыберите тип:",
                    call.message.chat.id, call.message.message_id, reply_markup=markup
                )

            elif data.startswith("search_type_"):
                type_ = data.split("_")[-1]
                shop_bot_states[f"{user_id}_search_type"] = type_
                shop_bot_states[user_id] = ShopBotState.SEARCH_INPUT
                bot_text = "Введите артикул:" if type_ == 'id' else "Введите имя товара:"
                shop_bot.edit_message_text(
                    bot_text, call.message.chat.id, call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("shop_search")
                )

            elif data == "search_filters":
                shop_bot_states[user_id] = ShopBotState.FILTER_MODE
                if f"{user_id}_filters" not in shop_bot_states:
                    shop_bot_states[f"{user_id}_filters"] = {
                        'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                    }
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "set_min_price":
                shop_bot_states[user_id] = ShopBotState.FILTER_MIN_PRICE
                shop_bot.edit_message_text(
                    "Введите минимальную цену:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("search_filters")
                )

            elif data == "set_max_price":
                shop_bot_states[user_id] = ShopBotState.FILTER_MAX_PRICE
                shop_bot.edit_message_text(
                    "Введите максимальную цену:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=keyboards.create_back_button_menu("search_filters")
                )

            elif data == "choose_category":
                categories = database.get_shop_categories(shop_id)
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                for cat_id, name in categories:
                    markup.add(telebot.types.InlineKeyboardButton(name, callback_data=f"filter_category_{cat_id}"))
                markup.add(telebot.types.InlineKeyboardButton("Все категории", callback_data="filter_category_none"))
                markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="search_filters"))
                shop_bot.edit_message_text(
                    "Выберите категорию:",
                    call.message.chat.id, call.message.message_id, reply_markup=markup
                )

            elif data.startswith("filter_category_"):
                cat_id  = data.split("_")[-1]
                filters = shop_bot_states.setdefault(f"{user_id}_filters", {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                })
                filters['category_id'] = None if cat_id == 'none' else int(cat_id)
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data.startswith("filter_sort_"):
                sort_type = data.replace("filter_sort_", "")
                filters   = shop_bot_states.setdefault(f"{user_id}_filters", {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                })
                filters['sort_by'] = f"price_{sort_type.split('_')[-1]}" if 'price' in sort_type else sort_type
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "reset_filters":
                shop_bot_states[f"{user_id}_filters"] = {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                }
                text, markup = create_filter_menu(user_id)
                shop_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

            elif data == "apply_filters":
                filters = shop_bot_states.get(f"{user_id}_filters", {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                })
                results = database.search_products(
                    shop_id, None, 'name',
                    filters['price_min'], filters['price_max'],
                    filters['category_id'], filters['sort_by']
                )
                shop_bot_states[f"{user_id}_current_list"]  = results
                shop_bot_states[f"{user_id}_current_title"] = "Результаты поиска"
                shop_bot_states[f"{user_id}_current_back"]  = "search_filters"
                show_products_list(call, results, "Результаты поиска", "search_filters")

        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 400 and 'message is not modified' in str(e):
                pass
            else:
                logging.error(f"Shop callback error: {e}")
                shop_bot.answer_callback_query(call.id, "Произошла ошибка. Попробуйте снова.")
        except Exception as e:
            logging.error(f"Shop callback error: {e}")
            shop_bot.answer_callback_query(call.id, f"Ошибка: {str(e)[:100]}")

    # ─────────────────── MESSAGE HANDLERS ───────────────────

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.ENTERING_QUANTITY
    )
    def handle_quantity_input(message):
        user_id = message.from_user.id
        if message.text.strip().lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.MAIN_MENU
            shop_bot.send_message(message.chat.id, "Отмена заказа", reply_markup=create_shop_main_menu())
            return

        try:
            quantity = int(message.text.strip())
            if quantity <= 0:
                raise ValueError
        except ValueError:
            shop_bot.send_message(
                message.chat.id,
                "Пожалуйста, введите корректное количество (целое число больше 0):"
            )
            return

        product_id = shop_bot_states.get(f"{user_id}_product_id")
        product    = database.get_product_info(product_id)
        if not product:
            shop_bot.send_message(message.chat.id, "❌ Товар не найден")
            shop_bot_states[user_id] = ShopBotState.MAIN_MENU
            return

        shop_bot_states[f"{user_id}_quantity"] = quantity
        shop_bot_states[user_id] = ShopBotState.ENTERING_PROMOCODE_DIRECT

        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_promo_direct"))

        shop_bot.send_message(
            message.chat.id,
            "🎟️ Введите промокод или нажмите «Пропустить»:",
            reply_markup=markup
        )

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.ENTERING_PROMOCODE_DIRECT
    )
    def handle_direct_promocode_input(message):
        user_id = message.from_user.id
        code    = message.text.strip()

        if code.lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.ENTERING_QUANTITY
            shop_bot.send_message(
                message.chat.id,
                "Введите количество товара:\n\nОтправьте «назад» для отмены"
            )
            return

        promo = database.validate_promocode(shop_id, code)
        if not promo:
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_promo_direct"))
            shop_bot.send_message(
                message.chat.id,
                "❌ Промокод не найден или недействителен. Попробуйте ещё раз или нажмите «Пропустить»:",
                reply_markup=markup
            )
            return

        shop_bot_states[f"{user_id}_promo_direct"] = promo
        disc_str = (f"-{int(promo['discount_value'])}%"
                    if promo['discount_type'] == 'percent'
                    else f"-{int(promo['discount_value'])}₽")
        shop_bot.send_message(
            message.chat.id,
            f"✅ Промокод <b>{promo['code']}</b> применён! Скидка: {disc_str}",
            parse_mode="HTML"
        )

        product_id = shop_bot_states.get(f"{user_id}_product_id")
        quantity   = shop_bot_states.get(f"{user_id}_quantity")
        _send_invoice_for_direct_buy(message.chat.id, user_id, product_id, quantity)

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.ENTERING_PROMOCODE
    )
    def handle_promocode_input(message):
        user_id = message.from_user.id
        code    = message.text.strip()

        if code.lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.VIEWING_CART
            shop_bot.send_message(
                message.chat.id, "Возврат в корзину.",
                reply_markup=keyboards.create_back_button_menu("view_cart")
            )
            return

        promo = database.validate_promocode(shop_id, code)
        if not promo:
            shop_bot.send_message(
                message.chat.id,
                "❌ Промокод не найден или недействителен. Попробуйте ещё раз или отправьте «назад»:"
            )
            return

        shop_bot_states[f"{user_id}_promo"] = promo
        shop_bot_states[user_id] = ShopBotState.VIEWING_CART
        disc_str = (f"-{int(promo['discount_value'])}%"
                    if promo['discount_type'] == 'percent'
                    else f"-{int(promo['discount_value'])}₽")
        shop_bot.send_message(
            message.chat.id,
            f"✅ Промокод <b>{promo['code']}</b> применён! Скидка: {disc_str}\n\nВернитесь в корзину для оформления.",
            parse_mode="HTML",
            reply_markup=keyboards.create_back_button_menu("view_cart")
        )

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.ENTERING_ADDRESS
    )
    def handle_cart_delivery_address(message):
        if message.text.strip().lower() == 'назад':
            shop_bot_states[message.from_user.id] = ShopBotState.VIEWING_CART
            shop_bot.send_message(
                message.chat.id, "❌ Оформление заказа отменено",
                reply_markup=keyboards.create_back_button_menu("view_cart")
            )
            return
        handle_delivery_address(message, None, shop_id, message.from_user.id)

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.REVIEW_TEXT
    )
    def handle_review_text(message):
        user_id = message.from_user.id
        if message.text.strip().lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.REVIEW_RATING
            markup = telebot.types.InlineKeyboardMarkup(row_width=5)
            for i in range(1, 6):
                markup.add(telebot.types.InlineKeyboardButton(f"{i}⭐", callback_data=f"shop_rating_{i}"))
            markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_reviews"))
            shop_bot.send_message(message.chat.id, "Оцените магазин от 1 до 5 звёзд:", reply_markup=markup)
            return

        rating = shop_bot_states.get(f"{user_id}_rating")
        if not rating:
            shop_bot_states[user_id] = ShopBotState.MAIN_MENU
            shop_bot.send_message(message.chat.id, "❌ Сессия истекла", reply_markup=create_shop_main_menu())
            return

        review_text = "" if message.text.strip() == "/skip" else message.text.strip()

        database.add_user(user_id, message.from_user.username or None)
        database.add_review(shop_id, user_id, rating, review_text)

        shop_bot.send_message(message.chat.id, "✅ Спасибо за ваш отзыв!", reply_markup=create_shop_main_menu())
        shop_bot_states[user_id] = ShopBotState.MAIN_MENU

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.SEARCH_INPUT
    )
    def handle_search_input(message):
        user_id = message.from_user.id
        query   = message.text.strip()

        if query.lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.SEARCH_MODE
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(telebot.types.InlineKeyboardButton("По имени", callback_data="search_type_name"))
            markup.add(telebot.types.InlineKeyboardButton("По артикулу", callback_data="search_type_id"))
            markup.add(telebot.types.InlineKeyboardButton("Фильтры", callback_data="search_filters"))
            markup.add(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
            shop_bot.send_message(message.chat.id, "🔍 Поиск товаров:\nВыберите тип:", reply_markup=markup)
            return

        type_ = shop_bot_states.get(f"{user_id}_search_type")
        if type_ == 'id' and not query.isdigit():
            shop_bot.send_message(message.chat.id, "Артикул должен быть числом. Попробуйте снова.")
            return

        results = database.search_products(shop_id, query, type_)
        shop_bot_states[f"{user_id}_current_list"]  = results
        shop_bot_states[f"{user_id}_current_title"] = "Результаты поиска"
        shop_bot_states[f"{user_id}_current_back"]  = "shop_search"

        class FakeCall:
            def __init__(self, msg):
                self.message   = msg
                self.from_user = msg.from_user
                self.data      = "search_results"

        show_products_list(FakeCall(message), results, "Результаты поиска", "shop_search")

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.FILTER_MIN_PRICE
    )
    def handle_filter_min_price(message):
        user_id = message.from_user.id
        text    = message.text.strip()
        if text.lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.FILTER_MODE
            t, markup = create_filter_menu(user_id)
            shop_bot.send_message(message.chat.id, t, reply_markup=markup)
            return
        try:
            value = float(text)
            if value < 0:
                raise ValueError
            shop_bot_states.setdefault(f"{user_id}_filters", {
                'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
            })['price_min'] = value
            shop_bot_states[user_id] = ShopBotState.FILTER_MODE
            t, markup = create_filter_menu(user_id)
            shop_bot.send_message(message.chat.id, t, reply_markup=markup)
        except ValueError:
            shop_bot.send_message(message.chat.id, "Некорректная цена. Введите положительное число или «назад».")

    @shop_bot.message_handler(
        func=lambda m: shop_bot_states.get(m.from_user.id) == ShopBotState.FILTER_MAX_PRICE
    )
    def handle_filter_max_price(message):
        user_id = message.from_user.id
        text    = message.text.strip()
        if text.lower() == 'назад':
            shop_bot_states[user_id] = ShopBotState.FILTER_MODE
            t, markup = create_filter_menu(user_id)
            shop_bot.send_message(message.chat.id, t, reply_markup=markup)
            return
        try:
            value = float(text)
            if value < 0:
                raise ValueError
            shop_bot_states.setdefault(f"{user_id}_filters", {
                'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
            })['price_max'] = value
            shop_bot_states[user_id] = ShopBotState.FILTER_MODE
            t, markup = create_filter_menu(user_id)
            shop_bot.send_message(message.chat.id, t, reply_markup=markup)
        except ValueError:
            shop_bot.send_message(message.chat.id, "Некорректная цена. Введите положительное число или «назад».")

    # ─────────────────── ОПЛАТА ───────────────────

    @shop_bot.message_handler(content_types=['successful_payment'])
    def handle_successful_payment(message):
        try:
            parts      = message.successful_payment.invoice_payload.split('_')
            product_id = int(parts[1])
            user_id    = int(parts[3])
            quantity   = int(parts[5])
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