"""
shop_bot.py  —  async версия бота магазина (aiogram 3.x)

Каждый магазин запускается как отдельный asyncio-Task.
active_shop_bots хранит словарь {shop_id: Bot} для рассылок.
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, Message, PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database
import keyboards
from states import ShopBotState

PRODUCTS_PER_PAGE = 5

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  Вспомогательные функции (чистые, не зависят от bot/dp)
# ──────────────────────────────────────────────────────────────────────────────

def _apply_promo(total: float, promo: dict) -> float:
    if promo['discount_type'] == 'percent':
        total = round(total * (1 - promo['discount_value'] / 100), 2)
    else:
        total = round(total - promo['discount_value'], 2)
    return max(0.01, total)


def _create_shop_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📦 Товары", callback_data="shop_products"),
        InlineKeyboardButton(text="🛒 Корзина", callback_data="view_cart"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Отзывы", callback_data="shop_reviews"),
        InlineKeyboardButton(text="🔍 Поиск", callback_data="shop_search"),
    )
    builder.row(InlineKeyboardButton(text="✨ Похожие магазины", callback_data="shop_recommendations"))
    return builder.as_markup()


async def _build_cart_message(shop_id: int, user_id: int, states: dict):
    items = await database.get_cart_items(shop_id, user_id)
    if not items:
        return None, None

    total_price = 0.0
    cart_text = "🛒 Ваша корзина:\n\n"
    builder = InlineKeyboardBuilder()

    for product_id, name, price, quantity in items:
        item_total = price * quantity
        total_price += item_total
        cart_text += f"📦 {name}\n   {price}₽ × {quantity} = {item_total}₽\n"
        builder.row(
            InlineKeyboardButton(text=f"❌ {name}", callback_data=f"remove_from_cart_{product_id}"),
            InlineKeyboardButton(text="➖", callback_data=f"decrease_cart_{product_id}"),
            InlineKeyboardButton(text=str(quantity), callback_data=f"change_quantity_{product_id}"),
            InlineKeyboardButton(text="➕", callback_data=f"increase_cart_{product_id}"),
        )

    cart_text += f"\n💰 Итого: {total_price:.2f}₽"

    promo = states.get(f"{user_id}_promo")
    if promo:
        discounted = _apply_promo(total_price, promo)
        disc_str = (f"-{int(promo['discount_value'])}%"
                    if promo['discount_type'] == 'percent'
                    else f"-{int(promo['discount_value'])}₽")
        saved = round(total_price - discounted, 2)
        cart_text += (f"\n🎟️ Промокод <b>{promo['code']}</b>: {disc_str}"
                      f"\n💸 Итого со скидкой: {discounted:.2f}₽ (экономия {saved:.2f}₽)")
        builder.row(InlineKeyboardButton(text="❌ Убрать промокод", callback_data="remove_promo"))
    else:
        builder.row(InlineKeyboardButton(text="🎟️ Ввести промокод", callback_data="enter_promo"))

    builder.row(
        InlineKeyboardButton(text="✅ Оформить заказ", callback_data="order_cart"),
        InlineKeyboardButton(text="🗑️ Очистить корзину", callback_data="clear_cart"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_main_menu"))
    return cart_text, builder.as_markup()


def _products_list_text_markup(products, title: str, back_data: str, page: int = 0):
    """Строит текст + клавиатуру списка товаров. Чистая функция."""
    total_pages = max(1, (len(products) + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE)
    start = page * PRODUCTS_PER_PAGE
    end = start + PRODUCTS_PER_PAGE
    page_products = products[start:end]

    text = f"{title} (Страница {page + 1}/{total_pages})\n\n"
    builder = InlineKeyboardBuilder()

    for product in page_products:
        pid   = product[0]
        name  = product[2] if len(product) > 5 else product[1]
        price = product[4] if len(product) > 5 else product[2]
        text += f"{name} (ID: {pid}) — {price}₽\n"
        builder.row(InlineKeyboardButton(
            text=f"Просмотреть {name}", callback_data=f"view_product_{pid}"
        ))

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"products_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️ Вперед", callback_data=f"products_page_{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data))
    return text, builder.as_markup()


async def _show_product_detail(chat_id: int, bot: Bot, product_id: int):
    product = await database.get_product_info(product_id)
    if not product:
        await bot.send_message(chat_id, "❌ Товар не найден")
        return

    name        = product[2]
    description = product[3]
    image_path  = product[5]
    display_price, orig_price, has_sale = database.get_product_display_price(product)
    price_line = (f"~~{orig_price}₽~~ 🔥 {display_price}₽"
                  if has_sale else f"{display_price}₽")
    caption = (
        f"📱 Артикул: {product_id}\n"
        f"📦 {name}\n"
        f"💰 {price_line}\n"
        f"📝 {description or 'Нет описания'}"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👛 Заказать", callback_data=f"buy_product_{product_id}"))
    builder.row(InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_list"))
    markup = builder.as_markup()

    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as photo:
            await bot.send_photo(chat_id, photo, caption=caption, reply_markup=markup)
    else:
        await bot.send_message(chat_id, caption, reply_markup=markup)


async def _send_invoice_for_direct_buy(
        chat_id: int, user_id: int, product_id: int, quantity: int,
        shop_id: int, bot: Bot, states: dict
):
    product = await database.get_product_info(product_id)
    if not product:
        await bot.send_message(chat_id, "❌ Товар не найден")
        return

    title       = product[2]
    description = product[3] or "Покупка товара"
    display_price, _, _ = database.get_product_display_price(product)
    total = float(display_price) * quantity

    promo = states.pop(f"{user_id}_promo_direct", None)
    label_suffix = ""
    if promo:
        discounted = _apply_promo(total, promo)
        saved = round(total - discounted, 2)
        disc_str = (f"-{int(promo['discount_value'])}%"
                    if promo['discount_type'] == 'percent'
                    else f"-{int(promo['discount_value'])}₽")
        label_suffix = f" ({disc_str})"
        total = discounted

        if total < 1.0:
            await database.use_promocode(promo['id'])
            await database.buy_product(shop_id, user_id, product_id, quantity, 0,
                                       'Цифровой товар (промокод 100%)')
            await bot.send_message(
                chat_id,
                f"✅ Товар <b>{title}</b> ×{quantity} оформлен бесплатно по промокоду "
                f"<b>{promo['code']}</b>!",
                parse_mode=ParseMode.HTML,
                reply_markup=_create_shop_main_menu()
            )
            states[user_id] = ShopBotState.MAIN_MENU
            return
        await database.use_promocode(promo['id'])

    payment_token = await database.get_paymaster_token_by_shop_id(shop_id)
    if not payment_token:
        await bot.send_message(
            chat_id,
            "❌ Оплата не настроена. Обратитесь к администратору магазина.",
            reply_markup=_create_shop_main_menu()
        )
        states[user_id] = ShopBotState.MAIN_MENU
        return

    price_kopecks = int(round(total * 100))
    price_kopecks = max(100, min(price_kopecks, 9_999_900))
    payload = f"product_{product_id}_user_{user_id}_quantity_{quantity}"

    await bot.send_invoice(
        chat_id,
        title=title,
        description=description + label_suffix,
        payload=payload,
        provider_token=payment_token,
        currency='RUB',
        prices=[LabeledPrice(label=f"{title} ×{quantity}{label_suffix}", amount=price_kopecks)],
        start_parameter='product',
    )
    states[user_id] = ShopBotState.MAIN_MENU


# ──────────────────────────────────────────────────────────────────────────────
#  Основная функция запуска бота магазина
# ──────────────────────────────────────────────────────────────────────────────

async def run_shop_bot(
        shop_id: int,
        bot_token: str,
        welcome_message: str,
        active_shop_bots: dict,
        manager_bot: Bot,
):
    """
    Запускает бот конкретного магазина как asyncio-Task.
    active_shop_bots[shop_id] = Bot(...) — для рассылок из manager-бота.
    """
    bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher()
    # per-bot состояния (замена глобального dict)
    states: dict = {}

    active_shop_bots[shop_id] = bot

    # ─── вспомогательные замыкания ───

    async def _show_reviews_page(chat_id: int, message_id: int, page: int = 0):
        stats = await database.get_shop_rating(shop_id)
        avg_rating        = float(stats[0] or 0)
        total_count        = stats[1] or 0
        reviews_list, _   = await database.get_shop_reviews(shop_id, page=page)
        stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
        text  = f"📊 Отзывы о магазине\nРейтинг: {stars} ({avg_rating:.1f}/5)\nВсего отзывов: {total_count}\n\n"
        if not reviews_list:
            text += "Отзывов пока нет. Будьте первыми!"
        else:
            for username, rating, review_text, date in reviews_list:
                user_display = f"@{username}" if username else "Аноним"
                s            = "⭐" * rating
                clean_date   = date.split(' ')[0] if date else ""
                content      = review_text or "Без текста"
                text += f"👤 {user_display} ({clean_date})\n{s}\n💬 {content}\n\n"
        markup = keyboards.create_shop_reviews_pagination(page, total_count)
        await bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)

    async def _handle_delivery_address_logic(message: Message, customer_id: int):
        delivery_address = message.text.strip()
        if not delivery_address:
            await message.answer("❌ Адрес не может быть пустым")
            return

        shop_info = await database.get_shop_info(shop_id)
        if not shop_info:
            await message.answer("❌ Магазин не найден")
            return

        payment_method = shop_info[4]
        items = await database.get_cart_items(shop_id, customer_id)
        if not items:
            await message.answer("🛒 Ваша корзина пуста")
            return

        total_price   = 0.0
        order_details = ""
        for pid, name, price, quantity in items:
            total_price   += price * quantity
            order_details += f"📦 {name} ×{quantity} — {price * quantity}₽\n"

        promo = states.get(f"{customer_id}_promo")
        if promo:
            discounted  = _apply_promo(total_price, promo)
            saved       = round(total_price - discounted, 2)
            disc_str    = (f"-{int(promo['discount_value'])}%"
                           if promo['discount_type'] == 'percent'
                           else f"-{int(promo['discount_value'])}₽")
            order_details += f"\n🎟️ Промокод {promo['code']} ({disc_str}): скидка {saved:.2f}₽"
            total_price = discounted
            await database.use_promocode(promo['id'])
            states.pop(f"{customer_id}_promo", None)

        order_ids = await database.place_cart_order(
            shop_id, customer_id, items, total_price, delivery_address
        )

        # Уведомляем администраторов через manager_bot
        admin_ids  = [shop_info[1]] + await database.get_shop_admins_ids(shop_id)
        notify_txt = (
            f"🆕 Новый заказ!\n\nМагазин: {shop_info[2]}\n{order_details}\n"
            f"💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n"
            f"👤 Покупатель: @{message.from_user.username or 'Не указан'}\n"
            f"💳 Способ оплаты: {payment_method}"
        )
        for aid in set(admin_ids):
            try:
                await manager_bot.send_message(aid, notify_txt)
            except Exception:
                pass

        if payment_method == 'online':
            shop_creds = shop_info[9]
            payment_url = None
            if shop_creds and ':' in shop_creds:
                sid, skey = shop_creds.split(':', 1)
                payment_url = await asyncio.get_event_loop().run_in_executor(
                    None, database.create_payment_link, total_price, order_ids[0], sid, skey
                )
            if payment_url:
                await message.answer(
                    f"🛒 Заказ оформлен!\n\n{order_details}\n"
                    f"💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n\n"
                    f"Оплатите заказ по ссылке:\n{payment_url}"
                )
            else:
                await message.answer(
                    f"🛒 Заказ оформлен!\n\n{order_details}\n"
                    f"💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n\n"
                    f"❌ Ошибка при создании платежной ссылки"
                )
        else:
            await message.answer(
                f"🛒 Заказ оформлен!\n\n{order_details}\n"
                f"💰 Итог: {total_price:.2f}₽\n🏠 Адрес: {delivery_address}\n"
                f"💳 Оплата при получении"
            )

        await database.clear_cart(shop_id, customer_id)
        states[customer_id] = ShopBotState.MAIN_MENU

    # ─── /start ───

    @dp.message(Command("start"))
    async def shop_start(message: Message):
        await database.add_user(message.from_user.id, message.from_user.username)
        await database.register_shop_user(shop_id, message.from_user.id)
        states[message.from_user.id] = ShopBotState.MAIN_MENU
        await message.answer(welcome_message, reply_markup=_create_shop_main_menu())

    # ─── CALLBACK HANDLER ───

    @dp.callback_query()
    async def shop_callback(call: CallbackQuery):
        user_id = call.from_user.id
        data    = call.data
        try:
            # ── Корзина: просмотр ──
            if data == "view_cart":
                states[user_id] = ShopBotState.VIEWING_CART
                cart_text, markup = await _build_cart_message(shop_id, user_id, states)
                if not cart_text:
                    await call.message.edit_text("🛒 Ваша корзина пуста", reply_markup=_create_shop_main_menu())
                    return
                await call.message.edit_text(cart_text, reply_markup=markup, parse_mode=ParseMode.HTML)

            # ── Корзина: изменить количество ──
            elif data.startswith("increase_cart_"):
                product_id = int(data.split("_")[-1])
                await database.update_cart_quantity(shop_id, user_id, product_id, 1)
                cart_text, markup = await _build_cart_message(shop_id, user_id, states)
                if not cart_text:
                    await call.message.edit_text("🛒 Ваша корзина пуста", reply_markup=_create_shop_main_menu())
                    return
                try:
                    await call.message.edit_text(cart_text, reply_markup=markup, parse_mode=ParseMode.HTML)
                except Exception:
                    pass

            elif data.startswith("decrease_cart_"):
                product_id = int(data.split("_")[-1])
                qty = await database.get_cart_quantity(shop_id, user_id, product_id)
                if qty > 1:
                    await database.update_cart_quantity(shop_id, user_id, product_id, -1)
                else:
                    await database.remove_from_cart(shop_id, user_id, product_id)
                cart_text, markup = await _build_cart_message(shop_id, user_id, states)
                if not cart_text:
                    await call.message.edit_text("🛒 Ваша корзина пуста", reply_markup=_create_shop_main_menu())
                    return
                try:
                    await call.message.edit_text(cart_text, reply_markup=markup, parse_mode=ParseMode.HTML)
                except Exception:
                    pass

            # ── Корзина: удалить ──
            elif data.startswith("remove_from_cart_"):
                product_id = int(data.split("_")[-1])
                await database.remove_from_cart(shop_id, user_id, product_id)
                await call.answer("Товар удалён из корзины")
                cart_text, markup = await _build_cart_message(shop_id, user_id, states)
                if not cart_text:
                    await call.message.edit_text("🛒 Ваша корзина пуста", reply_markup=_create_shop_main_menu())
                    return
                await call.message.edit_text(cart_text, reply_markup=markup, parse_mode=ParseMode.HTML)

            # ── Корзина: очистить ──
            elif data == "clear_cart":
                await database.clear_cart(shop_id, user_id)
                states.pop(f"{user_id}_promo", None)
                await call.message.edit_text("🛒 Корзина очищена", reply_markup=_create_shop_main_menu())

            # ── Корзина: оформить ──
            elif data == "order_cart":
                items = await database.get_cart_items(shop_id, user_id)
                if not items:
                    await call.message.edit_text("🛒 Ваша корзина пуста", reply_markup=_create_shop_main_menu())
                    return
                states[user_id] = ShopBotState.ENTERING_ADDRESS
                await call.message.edit_text(
                    "🏠 Введите адрес доставки:\n\nОтправьте «назад» для отмены",
                    reply_markup=keyboards.create_back_button_menu("view_cart")
                )

            # ── Промокод: ввести ──
            elif data == "enter_promo":
                states[user_id] = ShopBotState.ENTERING_PROMOCODE
                await call.message.edit_text(
                    "🎟️ Введите промокод:\n\nОтправьте «назад» для возврата в корзину",
                    reply_markup=keyboards.create_back_button_menu("view_cart")
                )

            # ── Промокод: убрать ──
            elif data == "remove_promo":
                states.pop(f"{user_id}_promo", None)
                await call.answer("Промокод убран")
                cart_text, markup = await _build_cart_message(shop_id, user_id, states)
                if not cart_text:
                    await call.message.edit_text("🛒 Ваша корзина пуста", reply_markup=_create_shop_main_menu())
                    return
                await call.message.edit_text(cart_text, reply_markup=markup, parse_mode=ParseMode.HTML)

            # ── Прямая покупка: пропустить промокод ──
            elif data == "skip_promo_direct":
                states.pop(f"{user_id}_promo_direct", None)
                product_id = states.get(f"{user_id}_product_id")
                quantity   = states.get(f"{user_id}_quantity")
                if not product_id or not quantity:
                    await call.answer("❌ Сессия истекла, начните заново")
                    states[user_id] = ShopBotState.MAIN_MENU
                    return
                await call.answer()
                await _send_invoice_for_direct_buy(
                    call.message.chat.id, user_id, product_id, quantity,
                    shop_id, bot, states
                )

            # ── Прямая покупка: начало ──
            elif data.startswith("buy_product_"):
                product_id = int(data.split("_")[-1])
                product = await database.get_product_info(product_id)
                if not product:
                    await call.answer("Товар не найден")
                    return
                states[user_id]                    = ShopBotState.ENTERING_QUANTITY
                states[f"{user_id}_product_id"]    = product_id
                await call.message.answer(
                    "Введите количество товара:\n\nОтправьте «назад» для отмены",
                    reply_markup=keyboards.create_back_button_menu(f"view_product_{product_id}")
                )
                await call.answer()

            # ── Корзина: добавить ──
            elif data.startswith("add_to_cart_"):
                product_id = int(data.split("_")[-1])
                if await database.add_to_cart(shop_id, user_id, product_id):
                    await call.answer("✅ Товар добавлен в корзину!")
                else:
                    await call.answer("❌ Ошибка при добавлении в корзину")

            # ── Товары ──
            elif data == "shop_products":
                categories = await database.get_shop_categories(shop_id)
                if not categories:
                    await call.message.edit_text("В магазине нет товаров.", reply_markup=_create_shop_main_menu())
                    return
                if len(categories) == 1:
                    products = await database.get_category_products(categories[0][0])
                    states[f"{user_id}_current_list"]  = products
                    states[f"{user_id}_current_title"] = "Товары"
                    states[f"{user_id}_current_back"]  = "shop_main_menu"
                    text, markup = _products_list_text_markup(products, "Товары", "shop_main_menu")
                    await call.message.edit_text(text, reply_markup=markup)
                else:
                    builder = InlineKeyboardBuilder()
                    for cat_id, name in categories:
                        builder.row(InlineKeyboardButton(text=name, callback_data=f"shop_category_{cat_id}"))
                    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_main_menu"))
                    await call.message.edit_text("Выберите категорию:", reply_markup=builder.as_markup())

            elif data.startswith("products_page_"):
                page      = int(data.split("_")[-1])
                products  = states.get(f"{user_id}_current_list", [])
                title     = states.get(f"{user_id}_current_title", "Товары")
                back_data = states.get(f"{user_id}_current_back", "shop_main_menu")
                text, markup = _products_list_text_markup(products, title, back_data, page)
                await call.message.edit_text(text, reply_markup=markup)

            elif data == "shop_main_menu":
                states[user_id] = ShopBotState.MAIN_MENU
                shop_info = await database.get_shop_info(shop_id)
                await call.message.edit_text(
                    shop_info[5] if shop_info else welcome_message,
                    reply_markup=_create_shop_main_menu()
                )

            # ── Отзывы ──
            elif data == "shop_reviews":
                states[user_id] = ShopBotState.BROWSING_REVIEWS
                await _show_reviews_page(call.message.chat.id, call.message.message_id, 0)

            elif data.startswith("reviews_next_") or data.startswith("reviews_prev_"):
                page = int(data.split("_")[-1])
                await _show_reviews_page(call.message.chat.id, call.message.message_id, page)

            elif data == "shop_leave_review":
                if await database.has_user_reviewed(shop_id, user_id):
                    await call.answer("Вы уже оставили отзыв для этого магазина")
                    return
                states[user_id] = ShopBotState.REVIEW_RATING
                builder = InlineKeyboardBuilder()
                for i in range(1, 6):
                    builder.add(InlineKeyboardButton(text=f"{i}⭐", callback_data=f"shop_rating_{i}"))
                builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_reviews"))
                await call.message.edit_text("Оцените магазин от 1 до 5 звёзд:", reply_markup=builder.as_markup())

            elif data.startswith("shop_rating_"):
                rating = int(data.split("_")[-1])
                states[user_id]              = ShopBotState.REVIEW_TEXT
                states[f"{user_id}_rating"]  = rating
                await call.message.edit_text(
                    f"Вы поставили оценку: {rating}⭐\n\n"
                    f"Теперь напишите отзыв (или /skip чтобы пропустить):\n\nОтправьте «назад» для отмены",
                    reply_markup=keyboards.create_back_button_menu("shop_leave_review")
                )

            # ── Категории ──
            elif data.startswith("shop_category_"):
                category_id = int(data.split("_")[-1])
                products    = await database.get_category_products(category_id)
                states[f"{user_id}_current_list"]  = products
                states[f"{user_id}_current_title"] = "Товары в категории"
                states[f"{user_id}_current_back"]  = "shop_products"
                text, markup = _products_list_text_markup(products, "Товары в категории", "shop_products")
                await call.message.edit_text(text, reply_markup=markup)

            elif data.startswith("view_product_"):
                product_id = int(data.split("_")[-1])
                await _show_product_detail(call.message.chat.id, bot, product_id)

            elif data == "back_to_list":
                try:
                    await call.message.delete()
                except Exception:
                    pass
                products  = states.get(f"{user_id}_current_list", [])
                title     = states.get(f"{user_id}_current_title", "Товары")
                back_data = states.get(f"{user_id}_current_back", "shop_main_menu")
                if not products:
                    await bot.send_message(call.message.chat.id, "Нет товаров.", reply_markup=_create_shop_main_menu())
                    return
                text, markup = _products_list_text_markup(products, title, back_data, 0)
                await bot.send_message(call.message.chat.id, text, reply_markup=markup)

            # ── Похожие магазины ──
            elif data == "shop_recommendations":
                shops = await database.get_similar_shops(shop_id)
                text  = "✨ Рекомендуемые магазины:\n\n"
                builder = InlineKeyboardBuilder()
                if not shops:
                    text += "К сожалению, пока нет рекомендаций."
                for s_id, name, username, rating, score, price_diff in shops:
                    stars  = "⭐" * int(rating)
                    reason = ""
                    if score > 5:   reason = "🔥 (Похожий выбор)"
                    elif score > 2: reason = "✅ (Схожие товары)"
                    text += f"{name} {stars} {float(rating):.1f} {reason}\n"
                    if username:
                        builder.row(InlineKeyboardButton(text=f"Перейти в {name}", url=f"https://t.me/{username}"))
                builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_main_menu"))
                await call.message.edit_text(text, reply_markup=builder.as_markup())

            # ── Поиск ──
            elif data == "shop_search":
                states[user_id] = ShopBotState.SEARCH_MODE
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(text="По имени", callback_data="search_type_name"),
                    InlineKeyboardButton(text="По артикулу", callback_data="search_type_id"),
                )
                builder.row(InlineKeyboardButton(text="Фильтры", callback_data="search_filters"))
                builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_main_menu"))
                await call.message.edit_text("🔍 Поиск товаров:\nВыберите тип:", reply_markup=builder.as_markup())

            elif data.startswith("search_type_"):
                type_ = data.split("_")[-1]
                states[f"{user_id}_search_type"] = type_
                states[user_id] = ShopBotState.SEARCH_INPUT
                prompt = "Введите артикул:" if type_ == 'id' else "Введите имя товара:"
                await call.message.edit_text(
                    prompt, reply_markup=keyboards.create_back_button_menu("shop_search")
                )

            elif data == "search_filters":
                states[user_id] = ShopBotState.FILTER_MODE
                states.setdefault(f"{user_id}_filters", {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                })
                text, markup = _build_filter_menu_sync(user_id, shop_id, states)
                await call.message.edit_text(text, reply_markup=markup)

            elif data == "set_min_price":
                states[user_id] = ShopBotState.FILTER_MIN_PRICE
                await call.message.edit_text(
                    "Введите минимальную цену:",
                    reply_markup=keyboards.create_back_button_menu("search_filters")
                )

            elif data == "set_max_price":
                states[user_id] = ShopBotState.FILTER_MAX_PRICE
                await call.message.edit_text(
                    "Введите максимальную цену:",
                    reply_markup=keyboards.create_back_button_menu("search_filters")
                )

            elif data == "choose_category":
                categories = await database.get_shop_categories(shop_id)
                builder = InlineKeyboardBuilder()
                for cat_id, name in categories:
                    builder.row(InlineKeyboardButton(text=name, callback_data=f"filter_category_{cat_id}"))
                builder.row(InlineKeyboardButton(text="Все категории", callback_data="filter_category_none"))
                builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="search_filters"))
                await call.message.edit_text("Выберите категорию:", reply_markup=builder.as_markup())

            elif data.startswith("filter_category_"):
                cat_id = data.split("_")[-1]
                f = states.setdefault(f"{user_id}_filters", {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                })
                f['category_id'] = None if cat_id == 'none' else int(cat_id)
                text, markup = _build_filter_menu_sync(user_id, shop_id, states)
                await call.message.edit_text(text, reply_markup=markup)

            elif data.startswith("filter_sort_"):
                sort_type = data.replace("filter_sort_", "")
                f = states.setdefault(f"{user_id}_filters", {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                })
                f['sort_by'] = f"price_{sort_type.split('_')[-1]}" if 'price' in sort_type else sort_type
                text, markup = _build_filter_menu_sync(user_id, shop_id, states)
                await call.message.edit_text(text, reply_markup=markup)

            elif data == "reset_filters":
                states[f"{user_id}_filters"] = {
                    'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
                }
                text, markup = _build_filter_menu_sync(user_id, shop_id, states)
                await call.message.edit_text(text, reply_markup=markup)

            elif data == "apply_filters":
                f = states.get(f"{user_id}_filters", {})
                results = await database.search_products(
                    shop_id, None, 'name',
                    f.get('price_min'), f.get('price_max'),
                    f.get('category_id'), f.get('sort_by', 'name')
                )
                states[f"{user_id}_current_list"]  = results
                states[f"{user_id}_current_title"] = "Результаты поиска"
                states[f"{user_id}_current_back"]  = "search_filters"
                text, markup = _products_list_text_markup(results, "Результаты поиска", "search_filters")
                await call.message.edit_text(text, reply_markup=markup)

            else:
                await call.answer()

        except Exception as e:
            if "message is not modified" in str(e):
                pass
            else:
                logger.error(f"Shop {shop_id} callback error: {e}", exc_info=True)
                await call.answer("Произошла ошибка. Попробуйте снова.")

    # ─── MESSAGE HANDLERS ───

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.ENTERING_QUANTITY))
    async def handle_quantity_input(message: Message):
        user_id = message.from_user.id
        text    = message.text.strip()
        if text.lower() == 'назад':
            states[user_id] = ShopBotState.MAIN_MENU
            await message.answer("Отмена заказа", reply_markup=_create_shop_main_menu())
            return
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Пожалуйста, введите корректное количество (целое число больше 0):")
            return
        product_id = states.get(f"{user_id}_product_id")
        product    = await database.get_product_info(product_id)
        if not product:
            await message.answer("❌ Товар не найден")
            states[user_id] = ShopBotState.MAIN_MENU
            return
        states[f"{user_id}_quantity"] = quantity
        states[user_id] = ShopBotState.ENTERING_PROMOCODE_DIRECT
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_promo_direct"))
        await message.answer("🎟️ Введите промокод или нажмите «Пропустить»:", reply_markup=builder.as_markup())

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.ENTERING_PROMOCODE_DIRECT))
    async def handle_direct_promocode(message: Message):
        user_id = message.from_user.id
        code    = message.text.strip()
        if code.lower() == 'назад':
            states[user_id] = ShopBotState.ENTERING_QUANTITY
            await message.answer("Введите количество товара:\n\nОтправьте «назад» для отмены")
            return
        promo = await database.validate_promocode(shop_id, code)
        if not promo:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_promo_direct"))
            await message.answer(
                "❌ Промокод не найден или недействителен. Попробуйте ещё раз или нажмите «Пропустить»:",
                reply_markup=builder.as_markup()
            )
            return
        states[f"{user_id}_promo_direct"] = promo
        disc_str = (f"-{int(promo['discount_value'])}%"
                    if promo['discount_type'] == 'percent'
                    else f"-{int(promo['discount_value'])}₽")
        await message.answer(
            f"✅ Промокод <b>{promo['code']}</b> применён! Скидка: {disc_str}",
            parse_mode=ParseMode.HTML
        )
        product_id = states.get(f"{user_id}_product_id")
        quantity   = states.get(f"{user_id}_quantity")
        await _send_invoice_for_direct_buy(
            message.chat.id, user_id, product_id, quantity, shop_id, bot, states
        )

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.ENTERING_PROMOCODE))
    async def handle_promocode(message: Message):
        user_id = message.from_user.id
        code    = message.text.strip()
        if code.lower() == 'назад':
            states[user_id] = ShopBotState.VIEWING_CART
            await message.answer("Возврат в корзину.", reply_markup=keyboards.create_back_button_menu("view_cart"))
            return
        promo = await database.validate_promocode(shop_id, code)
        if not promo:
            await message.answer("❌ Промокод не найден или недействителен. Попробуйте ещё раз или отправьте «назад»:")
            return
        states[f"{user_id}_promo"] = promo
        states[user_id] = ShopBotState.VIEWING_CART
        disc_str = (f"-{int(promo['discount_value'])}%"
                    if promo['discount_type'] == 'percent'
                    else f"-{int(promo['discount_value'])}₽")
        await message.answer(
            f"✅ Промокод <b>{promo['code']}</b> применён! Скидка: {disc_str}\n\nВернитесь в корзину для оформления.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.create_back_button_menu("view_cart")
        )

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.ENTERING_ADDRESS))
    async def handle_cart_address(message: Message):
        if message.text.strip().lower() == 'назад':
            states[message.from_user.id] = ShopBotState.VIEWING_CART
            await message.answer("❌ Оформление заказа отменено", reply_markup=keyboards.create_back_button_menu("view_cart"))
            return
        await _handle_delivery_address_logic(message, message.from_user.id)

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.REVIEW_TEXT))
    async def handle_review_text(message: Message):
        user_id = message.from_user.id
        text    = message.text.strip()
        if text.lower() == 'назад':
            states[user_id] = ShopBotState.REVIEW_RATING
            builder = InlineKeyboardBuilder()
            for i in range(1, 6):
                builder.add(InlineKeyboardButton(text=f"{i}⭐", callback_data=f"shop_rating_{i}"))
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_reviews"))
            await message.answer("Оцените магазин от 1 до 5 звёзд:", reply_markup=builder.as_markup())
            return
        rating = states.get(f"{user_id}_rating")
        if not rating:
            states[user_id] = ShopBotState.MAIN_MENU
            await message.answer("❌ Сессия истекла", reply_markup=_create_shop_main_menu())
            return
        review_text = "" if text == "/skip" else text
        await database.add_user(user_id, message.from_user.username or None)
        await database.add_review(shop_id, user_id, rating, review_text)
        await message.answer("✅ Спасибо за ваш отзыв!", reply_markup=_create_shop_main_menu())
        states[user_id] = ShopBotState.MAIN_MENU

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.SEARCH_INPUT))
    async def handle_search_input(message: Message):
        user_id = message.from_user.id
        query   = message.text.strip()
        if query.lower() == 'назад':
            states[user_id] = ShopBotState.SEARCH_MODE
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="По имени", callback_data="search_type_name"),
                InlineKeyboardButton(text="По артикулу", callback_data="search_type_id"),
            )
            builder.row(InlineKeyboardButton(text="Фильтры", callback_data="search_filters"))
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_main_menu"))
            await message.answer("🔍 Поиск товаров:\nВыберите тип:", reply_markup=builder.as_markup())
            return
        type_ = states.get(f"{user_id}_search_type")
        if type_ == 'id' and not query.isdigit():
            await message.answer("Артикул должен быть числом. Попробуйте снова.")
            return
        results = await database.search_products(shop_id, query, type_)
        states[f"{user_id}_current_list"]  = results
        states[f"{user_id}_current_title"] = "Результаты поиска"
        states[f"{user_id}_current_back"]  = "shop_search"
        text, markup = _products_list_text_markup(results, "Результаты поиска", "shop_search")
        await message.answer(text, reply_markup=markup)

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.FILTER_MIN_PRICE))
    async def handle_filter_min_price(message: Message):
        user_id = message.from_user.id
        text    = message.text.strip()
        if text.lower() == 'назад':
            states[user_id] = ShopBotState.FILTER_MODE
            t, markup = _build_filter_menu_sync(user_id, shop_id, states)
            await message.answer(t, reply_markup=markup)
            return
        try:
            value = float(text)
            if value < 0:
                raise ValueError
            states.setdefault(f"{user_id}_filters", {
                'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
            })['price_min'] = value
            states[user_id] = ShopBotState.FILTER_MODE
            t, markup = _build_filter_menu_sync(user_id, shop_id, states)
            await message.answer(t, reply_markup=markup)
        except ValueError:
            await message.answer("Некорректная цена. Введите положительное число или «назад».")

    @dp.message(F.func(lambda m: states.get(m.from_user.id) == ShopBotState.FILTER_MAX_PRICE))
    async def handle_filter_max_price(message: Message):
        user_id = message.from_user.id
        text    = message.text.strip()
        if text.lower() == 'назад':
            states[user_id] = ShopBotState.FILTER_MODE
            t, markup = _build_filter_menu_sync(user_id, shop_id, states)
            await message.answer(t, reply_markup=markup)
            return
        try:
            value = float(text)
            if value < 0:
                raise ValueError
            states.setdefault(f"{user_id}_filters", {
                'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
            })['price_max'] = value
            states[user_id] = ShopBotState.FILTER_MODE
            t, markup = _build_filter_menu_sync(user_id, shop_id, states)
            await message.answer(t, reply_markup=markup)
        except ValueError:
            await message.answer("Некорректная цена. Введите положительное число или «назад».")

    # ─── Платежи ───

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message):
        try:
            parts       = message.successful_payment.invoice_payload.split('_')
            product_id  = int(parts[1])
            user_id     = int(parts[3])
            quantity    = int(parts[5])
            total_price = message.successful_payment.total_amount / 100
            if await database.buy_product(shop_id, user_id, product_id, quantity, total_price, 'Цифровой товар (оплачено)'):
                await message.answer("✅ Товар успешно оплачен и добавлен в ваши покупки!")
            else:
                await message.answer("❌ Ошибка при обработке покупки")
        except Exception as e:
            logger.error(f"Ошибка обработки оплаты: {e}")
            await message.answer("❌ Произошла ошибка при обработке платежа")

    @dp.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery):
        await query.answer(ok=True)

    # ─── Запуск ───
    try:
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Ошибка в боте магазина {shop_id}: {e}")
    finally:
        active_shop_bots.pop(shop_id, None)
        await bot.session.close()


# ──────────────────────────────────────────────────────────────────────────────
#  Синхронный построитель меню фильтров (не требует await)
# ──────────────────────────────────────────────────────────────────────────────

def _build_filter_menu_sync(user_id: int, shop_id: int, states: dict):
    f = states.get(f"{user_id}_filters", {
        'price_min': None, 'price_max': None, 'category_id': None, 'sort_by': 'name'
    })
    text = "Фильтры:\n"
    text += f"Мин. цена: {f.get('price_min') or 'не указано'}\n"
    text += f"Макс. цена: {f.get('price_max') or 'не указано'}\n"
    cat_id = f.get('category_id')
    text += f"Категория: {'все' if not cat_id else str(cat_id)}\n"
    text += f"Сортировка: {f.get('sort_by', 'name')}\n"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Установить мин. цену", callback_data="set_min_price"))
    builder.row(InlineKeyboardButton(text="Установить макс. цену", callback_data="set_max_price"))
    builder.row(InlineKeyboardButton(text="Выбрать категорию", callback_data="choose_category"))
    builder.row(
        InlineKeyboardButton(text="Сорт. по цене ↑", callback_data="filter_sort_price_asc"),
        InlineKeyboardButton(text="Сорт. по цене ↓", callback_data="filter_sort_price_desc"),
    )
    builder.row(InlineKeyboardButton(text="Сорт. по популярности", callback_data="filter_sort_popularity"))
    builder.row(InlineKeyboardButton(text="Сорт. по новизне", callback_data="filter_sort_newest"))
    builder.row(
        InlineKeyboardButton(text="Применить", callback_data="apply_filters"),
        InlineKeyboardButton(text="Сбросить", callback_data="reset_filters"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_search"))
    return text, builder.as_markup()