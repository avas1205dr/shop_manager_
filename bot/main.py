"""
main.py  —  бот-менеджер (aiogram 3.x)

Запускает менеджер-бот + по одному asyncio-Task на каждый магазин.
"""

import asyncio
import logging
import os
import sqlite3
import uuid
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, FSInputFile, InlineKeyboardButton,
    InlineKeyboardMarkup, Message, ReplyKeyboardMarkup,
    KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database
import keyboards
import shop_bot as shop_bot_module
from states import UserState

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# ─── Глобальное состояние ───
user_states: dict   = {}          # user_id → UserState str  |  "uid_key" → value
active_shop_bots: dict = {}       # shop_id → Bot instance
shop_tasks: dict    = {}          # shop_id → asyncio.Task

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()


# ──────────────────────────────────────────────────────────────────────────────
#  Вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

def _uid(user_id: int, key: str) -> str:
    return f"{user_id}_{key}"


def _clear_product_state(user_id: int):
    for key in ("category_id", "product_name", "product_price",
                "product_description", "product_is_digital"):
        user_states.pop(_uid(user_id, key), None)


async def _start_shop_bot_task(shop_id: int, token: str, welcome_message: str):
    """Останавливает старую задачу (если есть) и запускает новую."""
    old_task = shop_tasks.pop(shop_id, None)
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(old_task), timeout=3)
        except Exception:
            pass

    task = asyncio.create_task(
        shop_bot_module.run_shop_bot(shop_id, token, welcome_message, active_shop_bots, bot),
        name=f"shop_bot_{shop_id}"
    )
    shop_tasks[shop_id] = task
    return task


async def _stop_shop_bot(shop_id: int):
    task = shop_tasks.pop(shop_id, None)
    if task and not task.done():
        task.cancel()
    active_shop_bots.pop(shop_id, None)


# ──────────────────────────────────────────────────────────────────────────────
#  Команды
# ──────────────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await database.add_user(message.from_user.id, message.from_user.username)
    user_states[message.from_user.id] = UserState.MAIN_MENU
    welcome_text = (
        "🛍️ Добро пожаловать в Shop Manager Bot!\n\n"
        "Этот бот поможет вам создать и управлять собственными магазинными ботами в Telegram.\n\n"
        "Для того чтобы настроить ваш магазин нужно:\n"
        "• Создать магазин в нашем боте\n"
        "• Создать бота через @BotFather, скопировать API-токен и вставить в разделе API бота\n"
        "• Также в @BotFather настроить Payments (инструкция есть в разделе Paymaster) и отправить токен\n"
        "• Добавить работников, товары, категории и т.д.\n"
        "• Готово!\n\n\n"
        "Возможности:\n"
        "• Создание неограниченного количества магазинов\n"
        "• Управление товарами и категориями\n"
        "• Настройка способов оплаты\n"
        "• Просмотр отзывов и рейтингов\n\n"
        "Выберите действие:"
    )
    await message.answer(welcome_text, reply_markup=keyboards.create_main_menu())


@dp.message(Command("get_id"))
async def cmd_get_id(message: Message):
    u = message.from_user
    first  = u.first_name or ""
    last   = u.last_name  or ""
    resp   = f"📋 Ваша информация:\n🆔 ID: {u.id}\n👤 Имя: {(first + ' ' + last).strip()}"
    if u.username:
        resp += f"\n📧 Username: @{u.username}"
    await message.answer(resp)


# ──────────────────────────────────────────────────────────────────────────────
#  Показ товара менеджером
# ──────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("show_product_"))
async def show_manager_product(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 5:
        await call.answer("Неверные данные")
        return
    product_id  = int(parts[2])
    category_id = int(parts[3])
    page        = int(parts[4])
    product = await database.get_product_info(product_id)
    if not product:
        await call.answer("Товар не найден")
        return
    name       = product[2]
    desc       = product[3]
    price      = product[4]
    image_path = product[5]
    text   = f"{name}\nЦена: {price}₽\nОписание: {desc or 'Нет'}"
    markup = keyboards.create_edit_product_menu(product_id, category_id, page)
    await call.message.delete()
    if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
        await bot.send_photo(call.message.chat.id, FSInputFile(image_path), caption=text, reply_markup=markup)
    else:
        await bot.send_message(call.message.chat.id, text, reply_markup=markup)


# ──────────────────────────────────────────────────────────────────────────────
#  Единый callback-handler (вся логика)
# ──────────────────────────────────────────────────────────────────────────────

@dp.callback_query()
async def callback_handler(call: CallbackQuery):
    user_id = call.from_user.id
    data    = call.data
    try:
        # ── Главное меню ──
        if data == "main_menu":
            user_states[user_id] = UserState.MAIN_MENU
            await call.message.edit_text(
                "🏠 Главное меню\n\nВыберите действие:",
                reply_markup=keyboards.create_main_menu()
            )

        # ── Рейтинг ──
        elif data == "reviews":
            await call.message.edit_text(
                "📊 Рейтинг магазинов\n\nМагазины отсортированы по рейтингу:",
                reply_markup=await keyboards.create_reviews_menu()
            )

        elif data.startswith("reviews_page_"):
            page = int(data.split("_")[-1])
            await call.message.edit_text(
                "📊 Рейтинг магазинов\n\nМагазины отсортированы по рейтингу:",
                reply_markup=await keyboards.create_reviews_menu(page)
            )

        elif data.startswith("shop_detail_"):
            shop_id   = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден")
                return
            stats      = await database.get_shop_rating(shop_id)
            avg_rating = float(stats[0] or 0)
            rev_count  = stats[1] or 0
            stars      = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
            builder    = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="reviews"))
            await call.message.edit_text(
                f"🏪 {shop_info[2]}\n\n⭐ Рейтинг: {stars} ({avg_rating:.1f}/5)\n📊 Отзывов: {rev_count}",
                reply_markup=builder.as_markup()
            )

        # ── Мои магазины ──
        elif data == "my_shops":
            await call.message.edit_text(
                "🏪 Ваши магазины:",
                reply_markup=await keyboards.create_my_shops_menu(user_id)
            )

        elif data == "create_shop":
            user_states[user_id] = UserState.CREATING_SHOP
            await call.message.edit_text(
                "Введите название для нового магазина (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu("my_shops")
            )

        elif data.startswith("manage_shop_"):
            shop_id   = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден")
                return
            token_status = "Установлен" if shop_info[3] else "Не установлен"
            await call.message.edit_text(
                f"⚙️ Управление магазином: {shop_info[2]}\n\n"
                f"🔑 Токен API: {token_status}\n\nВыберите действие для настройки:",
                reply_markup=keyboards.create_shop_management_menu(shop_id)
            )

        # ── Токен бота ──
        elif data.startswith("edit_token_"):
            shop_id   = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            cur_token = shop_info[3] if shop_info and shop_info[3] else "Не установлен"
            user_states[user_id]                  = UserState.EDITING_TOKEN
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                f"🔑 Токен API бота\n\nТекущий токен: {cur_token}\n\n"
                f"Введите новый токен (минимум 30 символов):\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
            )

        # ── PayMaster ──
        elif data.startswith("paymaster_token_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id]                  = UserState.EDITING_PAYMASTER
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                "💳 Настройка PayMaster:\n\n"
                "1. Перейдите в @BotFather\n"
                "2. Выберите вашего бота\n"
                "3. Перейдите в раздел \"Payments\"\n"
                "4. Выберите \"PayMaster\" как провайдера платежей\n"
                "5. Скопируйте полученный токен\n\n"
                "Отправьте токен в следующем сообщении или 'назад' для отмены:",
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
            )

        # ── Приветствие ──
        elif data.startswith("edit_welcome_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id]                  = UserState.EDITING_WELCOME
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                "Введите новое приветственное сообщение для покупателей (минимум 5 символов):\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
            )

        # ── Способ оплаты ──
        elif data.startswith("payment_method_"):
            shop_id = int(data.split("_")[-1])
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="Оплата на месте", callback_data=f"set_payment_cash_{shop_id}"),
                InlineKeyboardButton(text="Онлайн-оплата (ЮKassa)", callback_data=f"set_payment_online_{shop_id}"),
            )
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
            await call.message.edit_text("Выберите способ оплаты:", reply_markup=builder.as_markup())

        elif data.startswith("set_payment_"):
            shop_id      = int(data.split("_")[-1])
            payment_type = "cash_on_delivery" if "cash" in data else "online"
            if payment_type == "cash_on_delivery":
                await database.update_payment_method(shop_id, payment_type)
                await call.message.edit_text(
                    "✅ Способ оплаты установлен: Оплата на месте",
                    reply_markup=keyboards.create_shop_management_menu(shop_id)
                )
            else:
                user_states[user_id]                  = UserState.EDITING_PAYMENT
                user_states[_uid(user_id, "shop_id")] = shop_id
                await call.message.edit_text(
                    "Настройка ЮKassa:\n"
                    "1. Зарегистрируйтесь на https://yookassa.ru/\n"
                    "2. Получите Shop ID и Secret Key в личном кабинете\n"
                    "3. Введите данные в формате: ShopID:SecretKey\n"
                    "Пример: 123456:live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx\n\n"
                    "Отправьте 'назад' для отмены",
                    reply_markup=keyboards.create_back_button_menu(f"payment_method_{shop_id}")
                )

        # ── Удаление магазина ──
        elif data.startswith("delete_shop_"):
            shop_id   = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден")
                return
            if shop_info[1] != user_id:
                await call.answer("Только создатель магазина может его удалить")
                return
            await database.delete_shop(shop_id)
            await _stop_shop_bot(shop_id)
            await call.message.edit_text(
                "✅ Магазин удалён",
                reply_markup=await keyboards.create_my_shops_menu(user_id)
            )

        # ── Управление товарами ──
        elif data.startswith("manage_products_"):
            shop_id = int(data.split("_")[-1])
            await call.message.edit_text(
                "📦 Управление товарами\n\nВыберите раздел:",
                reply_markup=await keyboards.create_categories_menu(shop_id)
            )

        elif data.startswith("create_category_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id]                  = UserState.CREATING_CATEGORY
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                "Введите название для нового раздела (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu(f"manage_products_{shop_id}")
            )

        elif data.startswith("category_"):
            category_id = int(data.split("_")[-1])
            await call.message.edit_text(
                "📦 Действия с разделом:",
                reply_markup=await keyboards.create_category_actions_menu(category_id)
            )

        elif data.startswith("view_products_"):
            category_id = int(data.split("_")[-1])
            await call.message.edit_text(
                "📦 Товары в разделе:",
                reply_markup=await keyboards.create_products_menu(category_id)
            )

        elif data.startswith("add_product_"):
            category_id = int(data.split("_")[-1])
            user_states[user_id]                     = UserState.PRODUCT_NAME
            user_states[_uid(user_id, "category_id")] = category_id
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"category_{category_id}"))
            await call.message.edit_text(
                "Введите название товара (минимум 2 символа):",
                reply_markup=builder.as_markup()
            )

        elif data.startswith("product_"):
            parts = data.split("_")
            if len(parts) < 4:
                await call.answer("Неверные данные")
                return
            product_id  = int(parts[1])
            category_id = int(parts[2])
            page        = int(parts[3])
            user_states[user_id] = UserState.EDITING_PRODUCT
            await call.message.edit_text(
                "Выберите действие:",
                reply_markup=keyboards.create_edit_product_menu(product_id, category_id, page)
            )

        elif data.startswith("edit_name_") or data.startswith("edit_price_") \
                or data.startswith("edit_desc_") or data.startswith("edit_photo_") \
                or data.startswith("edit_sale_"):
            parts = data.split("_")
            if len(parts) < 5:
                await call.answer("Неверные данные")
                return
            edit_type   = parts[1]
            product_id  = int(parts[2])
            category_id = int(parts[3])
            page        = int(parts[4])
            user_states[user_id]                     = UserState.EDITING_PRODUCT
            user_states[_uid(user_id, "edit_type")]  = edit_type
            user_states[_uid(user_id, "product_id")] = product_id
            user_states[_uid(user_id, "category_id")] = category_id
            user_states[_uid(user_id, "page")]        = page
            product_info = await database.get_product_info(product_id)
            sale_hint    = f"Текущая: {product_info[4]}₽" if product_info else ""
            prompt = {
                "name":  "Введите новое название (мин 2 символа):\n\nОтправьте 'назад' для отмены",
                "price": "Введите новую цену (положительное число):\n\nОтправьте 'назад' для отмены",
                "desc":  "Введите новое описание:\n\nОтправьте 'назад' для отмены",
                "photo": "Отправьте новое фото или текст 'пропустить'/'стандартное':\n\nОтправьте 'назад' для отмены",
                "sale":  f"💸 Акционная цена ({sale_hint})\n\nВведите новую цену меньше обычной.\nЧтобы убрать скидку — отправьте '-'\n\nОтправьте 'назад' для отмены",
            }[edit_type]
            try:
                await call.message.delete()
            except Exception:
                pass
            await bot.send_message(
                call.message.chat.id, prompt,
                reply_markup=keyboards.create_back_button_menu(f"product_{product_id}_{category_id}_{page}")
            )

        elif data.startswith("delete_product_"):
            parts = data.split("_")
            if len(parts) < 5:
                await call.answer("Неверные данные")
                return
            product_id  = int(parts[2])
            category_id = int(parts[3])
            page        = int(parts[4])
            await database.delete_product(product_id)
            await call.answer("✅ Товар удалён")
            await call.message.edit_text(
                "📦 Товары в разделе:",
                reply_markup=await keyboards.create_products_menu(category_id, page)
            )

        elif data.startswith("back_to_products_"):
            parts       = data.split("_")
            category_id = int(parts[3])
            page        = int(parts[4])
            await call.message.delete()
            await bot.send_message(
                call.message.chat.id, "📦 Товары в разделе:",
                reply_markup=await keyboards.create_products_menu(category_id, page)
            )

        elif data.startswith("prev_page_") or data.startswith("next_page_"):
            parts       = data.split("_")
            category_id = int(parts[2])
            page        = int(parts[3])
            await call.message.edit_text(
                "📦 Товары в разделе:",
                reply_markup=await keyboards.create_products_menu(category_id, page)
            )

        elif data.startswith("all_products_"):
            shop_id  = int(data.split("_")[-1])
            products = await database.get_all_shop_products(shop_id)
            if not products:
                await call.message.edit_text(
                    "В магазине нет товаров.",
                    reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
                )
                return
            text = "📦 Все товары в магазине:\n\n"
            for cat_name, name, price, desc in products:
                text += f"[{cat_name}] {name} - {price}₽"
                text += f"\n   {desc}\n" if desc else "\n"
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
            await call.message.edit_text(text, reply_markup=builder.as_markup())

        # ── Категория: редактирование ──
        elif data.startswith("edit_category_name_"):
            category_id = int(data.split("_")[-1])
            user_states[user_id]                       = UserState.EDITING_CATEGORY_NAME
            user_states[_uid(user_id, "category_id")] = category_id
            await call.message.edit_text(
                "Введите новое название для раздела (минимум 2 символа):\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu(f"category_{category_id}")
            )

        elif data.startswith("delete_category_"):
            category_id = int(data.split("_")[-1])
            shop_id     = await database.get_shop_id_by_category(category_id)
            if await database.delete_category(category_id):
                await call.message.edit_text(
                    "✅ Раздел удалён",
                    reply_markup=await keyboards.create_categories_menu(shop_id)
                )
            else:
                await call.answer("❌ Ошибка при удалении раздела")

        # ── Работники ──
        elif data.startswith("workers_"):
            shop_id = int(data.split("_")[1])
            await call.message.edit_text(
                "👥 Управление работниками магазина:",
                reply_markup=keyboards.create_workers_menu(shop_id)
            )

        elif data.startswith("add_worker_"):
            shop_id = int(data.split("_")[2])
            user_states[user_id]                  = UserState.ADDING_WORKER
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                "👤 Добавление работника\n\nВведите @username или ID пользователя\n\n",
                reply_markup=keyboards.create_back_button_menu(f"workers_{shop_id}")
            )

        elif data.startswith("list_workers_"):
            shop_id = int(data.split("_")[2])
            workers = await database.get_shop_workers(shop_id)
            if not workers:
                await call.message.edit_text(
                    "В магазине нет работников",
                    reply_markup=keyboards.create_workers_menu(shop_id)
                )
                return
            resp = "👥 Список работников:\n\n"
            for wid, uname in workers:
                resp += f"• @{uname} (ID: {wid})\n" if uname else f"• ID: {wid}\n"
            await call.message.edit_text(resp, reply_markup=keyboards.create_workers_menu(shop_id))

        elif data.startswith("remove_worker_"):
            shop_id   = int(data.split("_")[2])
            workers   = await database.get_shop_workers(shop_id)
            shop_info = await database.get_shop_info(shop_id)
            owner_id  = shop_info[1]
            non_owners = [(w[0], w[1]) for w in workers if w[0] != owner_id]
            if not non_owners:
                await call.answer("Нет работников для увольнения")
                return
            await call.message.edit_text(
                "Выберите работника для увольнения:",
                reply_markup=keyboards.create_remove_worker_menu(shop_id, non_owners)
            )

        elif data.startswith("confirm_remove_"):
            parts = data.split("_")
            if "step2" in parts:
                shop_id   = int(parts[3])
                worker_id = int(parts[4])
                await call.message.edit_text(
                    "А вдруг у него семья?😭",
                    reply_markup=keyboards.create_confirm_remove_step2_menu(shop_id, worker_id)
                )
            else:
                shop_id   = int(parts[2])
                worker_id = int(parts[3])
                await call.message.edit_text(
                    "Вы уверены, что хотите уволить этого работника (может не надо)?",
                    reply_markup=keyboards.create_confirm_remove_menu(shop_id, worker_id)
                )

        elif data.startswith("do_remove_"):
            parts     = data.split("_")
            shop_id   = int(parts[2])
            worker_id = int(parts[3])
            shop_info = await database.get_shop_info(shop_id)
            if await database.remove_worker(shop_id, worker_id):
                try:
                    await bot.send_message(worker_id,
                        f"Вы были удалены как работник из магазина '{shop_info[2]}'")
                except Exception:
                    pass
                admins = await database.get_shop_workers(shop_id)
                for aid, _ in admins:
                    try:
                        await bot.send_message(aid,
                            f"Был уволен работник ID:{worker_id} из магазина '{shop_info[2]}'")
                    except Exception:
                        pass
                await call.answer("✅ Работник был отправлен на рынок труда")
            else:
                await call.answer("❌ Ошибка при удалении")
            await call.message.edit_text(
                "👥 Управление работниками магазина:",
                reply_markup=keyboards.create_workers_menu(shop_id)
            )

        # ── Заказы ──
        elif data.startswith("view_orders_"):
            shop_id = int(data.split("_")[-1])
            orders  = await database.get_shop_orders(shop_id)
            if not orders:
                await call.message.edit_text(
                    "📋 Заказы\n\nЗаказов пока нет",
                    reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
                )
                return
            await call.message.edit_text(
                "📋 Заказы\n\n",
                reply_markup=keyboards.create_orders_menu(shop_id, orders)
            )

        elif data.startswith("orders_page_"):
            parts   = data.split("_")
            shop_id = int(parts[2])
            page    = int(parts[3])
            orders  = await database.get_shop_orders(shop_id)
            await call.message.edit_text(
                "📋 Заказы\n\n",
                reply_markup=keyboards.create_orders_menu(shop_id, orders, page)
            )

        elif data.startswith("order_detail_"):
            await call.answer("Функция в разработке")

        # ── Рассылка ──
        elif data.startswith("broadcast_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id]                  = UserState.BROADCAST_MESSAGE
            user_states[_uid(user_id, "shop_id")] = shop_id
            count = len(await database.get_shop_user_ids(shop_id))
            await call.message.edit_text(
                f"📢 Рассылка сообщений\n\nПодписчиков бота: {count}\n\n"
                "Отправьте сообщение (текст, фото или видео), которое хотите разослать всем пользователям.\n"
                "Отправьте 'назад' для отмены.",
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
            )

        # ── Промокоды ──
        elif data.startswith("manage_promocodes_"):
            shop_id = int(data.split("_")[-1])
            promos  = await database.get_shop_promocodes(shop_id)
            header  = ("🎟️ Промокоды магазина\n\nНажмите на промокод — он удалится."
                       if promos else "🎟️ Промокодов пока нет.")
            await call.message.edit_text(
                header, reply_markup=keyboards.create_promocodes_menu(shop_id, promos)
            )

        elif data.startswith("add_promocode_"):
            shop_id = int(data.split("_")[-1])
            user_states[user_id]                  = UserState.ADDING_PROMO_CODE
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                "🎟️ Создание промокода\n\nШаг 1/3: Введите текст кода (например SALE20):\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu(f"manage_promocodes_{shop_id}")
            )

        elif data.startswith("promo_type_percent_") or data.startswith("promo_type_fixed_"):
            parts   = data.split("_")
            shop_id = int(parts[-1])
            dtype   = "percent" if "percent" in data else "fixed"
            user_states[_uid(user_id, "promo_type")] = dtype
            user_states[user_id] = UserState.ADDING_PROMO_VALUE
            hint = ("(0–100, например 15 = скидка 15%)"
                    if dtype == "percent"
                    else "(в рублях, например 200 = скидка 200₽)")
            await call.message.edit_text(
                f"Шаг 3/3: Введите размер скидки {hint}:\n\nОтправьте 'назад' для отмены",
                reply_markup=keyboards.create_back_button_menu(f"manage_promocodes_{shop_id}")
            )

        elif data.startswith("delete_promo_"):
            parts   = data.split("_")
            promo_id = int(parts[2])
            shop_id  = int(parts[3])
            await database.deactivate_promocode(promo_id)
            await call.answer("✅ Промокод удалён")
            promos  = await database.get_shop_promocodes(shop_id)
            header  = ("🎟️ Промокоды магазина\n\nНажмите на промокод — он удалится."
                       if promos else "🎟️ Промокодов пока нет.")
            await call.message.edit_text(
                header, reply_markup=keyboards.create_promocodes_menu(shop_id, promos)
            )

        # ── default image / skip image ──
        elif data == "default_image":
            category_id = user_states.get(_uid(user_id, "category_id"))
            product_name  = user_states.get(_uid(user_id, "product_name"))
            product_price = user_states.get(_uid(user_id, "product_price"))
            description   = user_states.get(_uid(user_id, "product_description"))
            is_digital    = user_states.get(_uid(user_id, "product_is_digital"), True)
            pid = await database.add_product(
                category_id, product_name, product_price,
                "work_photos/default_not_image.jpg", is_digital, description
            )
            if pid:
                await call.answer("✅ Товар добавлен со стандартным изображением")
                await bot.send_message(call.message.chat.id, "📦 Товары в разделе:",
                                       reply_markup=await keyboards.create_products_menu(category_id))
            else:
                await call.answer("❌ Ошибка при добавлении товара")
            user_states[user_id] = UserState.SHOP_MENU
            _clear_product_state(user_id)

        elif data == "skip_image":
            category_id   = user_states.get(_uid(user_id, "category_id"))
            product_name  = user_states.get(_uid(user_id, "product_name"))
            product_price = user_states.get(_uid(user_id, "product_price"))
            description   = user_states.get(_uid(user_id, "product_description"))
            is_digital    = user_states.get(_uid(user_id, "product_is_digital"), True)
            pid = await database.add_product(
                category_id, product_name, product_price, None, is_digital, description
            )
            if pid:
                await call.answer("✅ Товар добавлен без изображения")
                await bot.send_message(call.message.chat.id, "📦 Товары в разделе:",
                                       reply_markup=await keyboards.create_products_menu(category_id))
            else:
                await call.answer("❌ Ошибка при добавлении товара")
            user_states[user_id] = UserState.SHOP_MENU
            _clear_product_state(user_id)

        elif data == "back_from_desc":
            user_states[user_id] = UserState.PRODUCT_PRICE
            await call.message.edit_text(
                "Введите цену товара (только положительное число):\n\nОтправьте 'назад' для отмены"
            )

        elif data == "back_from_image":
            user_states[user_id] = UserState.PRODUCT_DESCRIPTION
            await call.message.edit_text(
                "Введите описание товара (или '-' чтобы пропустить):"
            )

        else:
            await call.answer()

    except Exception as e:
        if "message is not modified" in str(e):
            pass
        else:
            logger.error(f"Callback error [{data}]: {e}", exc_info=True)
            await call.answer("Произошла ошибка. Попробуйте снова.")


# ──────────────────────────────────────────────────────────────────────────────
#  Message handlers
# ──────────────────────────────────────────────────────────────────────────────

@dp.message(F.content_types(["text", "photo", "video", "document"]),
            F.func(lambda m: user_states.get(m.from_user.id) == UserState.BROADCAST_MESSAGE))
async def execute_broadcast(message: Message):
    user_id = message.from_user.id
    shop_id = user_states.get(_uid(user_id, "shop_id"))

    if message.text and message.text.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("Рассылка отменена",
                             reply_markup=keyboards.create_shop_management_menu(shop_id))
        return

    if shop_id not in active_shop_bots:
        await message.answer("❌ Бот магазина не запущен. Проверьте токен.")
        return

    shop_bot_instance = active_shop_bots[shop_id]
    users = await database.get_shop_user_ids(shop_id)
    if not users:
        await message.answer("❌ Нет пользователей для рассылки.")
        return

    await message.answer(f"🚀 Начинаю рассылку для {len(users)} пользователей...")

    async def _do_broadcast():
        success_count = 0
        fail_count    = 0
        cached_file_id: Optional[str] = None

        for uid in users:
            try:
                if message.content_type == 'photo':
                    fid = cached_file_id or message.photo[-1].file_id
                    sent = await shop_bot_instance.send_photo(uid, fid, caption=message.caption)
                    cached_file_id = cached_file_id or sent.photo[-1].file_id
                elif message.content_type == 'video':
                    fid = cached_file_id or message.video.file_id
                    sent = await shop_bot_instance.send_video(uid, fid, caption=message.caption)
                    cached_file_id = cached_file_id or sent.video.file_id
                elif message.content_type == 'document':
                    fid = cached_file_id or message.document.file_id
                    sent = await shop_bot_instance.send_document(uid, fid, caption=message.caption)
                    cached_file_id = cached_file_id or sent.document.file_id
                else:
                    await shop_bot_instance.send_message(uid, message.text)
                success_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Broadcast fail uid={uid}: {e}")
                fail_count += 1

        try:
            await bot.send_message(
                user_id,
                f"✅ Рассылка завершена!\nУспешно: {success_count}\nОшибок (блокировок): {fail_count}",
                reply_markup=keyboards.create_shop_management_menu(shop_id)
            )
        except Exception:
            pass

    asyncio.create_task(_do_broadcast())
    user_states[user_id] = UserState.SHOP_MENU


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.ADDING_WORKER))
async def add_worker_handler(message: Message):
    user_id     = message.from_user.id
    shop_id     = user_states.get(_uid(user_id, "shop_id"))
    admin_input = message.text.strip()

    if admin_input.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("❌ Добавление работника отменено",
                             reply_markup=keyboards.create_shop_management_menu(shop_id))
        return

    admin_user_id = None
    username      = None

    if admin_input.startswith('@'):
        username = admin_input[1:]
        row = await database.get_user_by_username(username)
        if row:
            admin_user_id = row[0]
        else:
            await message.answer(
                "❌ Пользователь не найден в базе. Попросите его сначала написать боту /get_id"
            )
            return
    else:
        try:
            admin_user_id = int(admin_input)
        except ValueError:
            await message.answer("❌ Введите корректный @username или ID пользователя")
            return

    if not admin_user_id:
        await message.answer("❌ Не удалось определить пользователя")
        return

    shop_info = await database.get_shop_info(shop_id)
    if not shop_info:
        await message.answer("❌ Магазин не найден")
        return
    if shop_info[1] != user_id:
        await message.answer("❌ Только создатель магазина может добавлять работников")
        return
    if admin_user_id == shop_info[1]:
        await message.answer("❌ Вы уже являетесь создателем этого магазина")
        return

    await database.add_user(admin_user_id, username)
    added = await database.add_worker(shop_id, admin_user_id)

    if added:
        bot_info = await bot.get_me()
        try:
            await bot.send_message(
                admin_user_id,
                f"🎉 Вы были добавлены как работник магазина '{shop_info[2]}'!\n\n"
                f"Теперь вы можете управлять этим магазином через @{bot_info.username}"
            )
        except Exception:
            pass
        await message.answer(
            f"✅ Пользователь {admin_input} добавлен как работник",
            reply_markup=keyboards.create_shop_management_menu(shop_id)
        )
    else:
        await message.answer(
            f"ℹ️ Пользователь {admin_input} уже является работником",
            reply_markup=keyboards.create_shop_management_menu(shop_id)
        )
    user_states[user_id] = UserState.SHOP_MENU


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.EDITING_PAYMASTER))
async def save_paymaster_token(message: Message):
    user_id = message.from_user.id
    shop_id = user_states.get(_uid(user_id, "shop_id"))
    token   = message.text.strip()

    if token.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("❌ Настройка PayMaster отменена",
                             reply_markup=keyboards.create_shop_management_menu(shop_id))
        return
    if len(token) < 10:
        await message.answer("❌ Токен слишком короткий. Попробуйте снова или отправьте 'назад' для отмены")
        return
    if await database.update_paymaster_token(shop_id, token):
        await message.answer("✅ PayMaster токен успешно сохранен!",
                             reply_markup=keyboards.create_shop_management_menu(shop_id))
    else:
        await message.answer("❌ Ошибка при сохранении токена")
    user_states[user_id] = UserState.SHOP_MENU


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.EDITING_PAYMENT))
async def save_payment_credentials(message: Message):
    user_id     = message.from_user.id
    shop_id     = user_states.get(_uid(user_id, "shop_id"))
    credentials = message.text.strip()

    if credentials.lower() == 'назад':
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("❌ Настройка оплаты отменена",
                             reply_markup=keyboards.create_shop_management_menu(shop_id))
        return
    if ":" not in credentials:
        await message.answer("❌ Формат неверный. Введите ShopID:SecretKey или 'назад' для отмены")
        return
    await database.update_payment_method(shop_id, "online", credentials)
    await message.answer("✅ Настройки ЮKassa сохранены!",
                         reply_markup=keyboards.create_shop_management_menu(shop_id))
    user_states[user_id] = UserState.SHOP_MENU


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.ADDING_PROMO_CODE))
async def handle_promo_code_input(message: Message):
    user_id = message.from_user.id
    shop_id = user_states.get(_uid(user_id, "shop_id"))
    text    = message.text.strip()

    if text.lower() == "назад":
        user_states[user_id] = UserState.SHOP_MENU
        promos = await database.get_shop_promocodes(shop_id)
        header = ("🎟️ Промокоды магазина\n\nНажмите на промокод — он удалится."
                  if promos else "🎟️ Промокодов пока нет.")
        await message.answer(header, reply_markup=keyboards.create_promocodes_menu(shop_id, promos))
        return

    code = text.upper()
    if len(code) < 2 or len(code) > 20:
        await message.answer("❌ Код должен быть от 2 до 20 символов. Попробуйте снова:")
        return

    user_states[_uid(user_id, "promo_code")] = code
    await message.answer(
        f"Код: <b>{code}</b>\n\nШаг 2/3: Выберите тип скидки:",
        reply_markup=keyboards.create_promo_type_menu(shop_id)
    )


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.ADDING_PROMO_VALUE))
async def handle_promo_value_input(message: Message):
    user_id = message.from_user.id
    shop_id = user_states.get(_uid(user_id, "shop_id"))
    text    = message.text.strip()

    if text.lower() == "назад":
        user_states[user_id] = UserState.ADDING_PROMO_CODE
        await message.answer("Введите текст промокода:",
                             reply_markup=keyboards.create_back_button_menu(f"manage_promocodes_{shop_id}"))
        return

    try:
        value = float(text)
        dtype = user_states.get(_uid(user_id, "promo_type"), "percent")
        if value <= 0 or (dtype == "percent" and value > 100):
            raise ValueError()
    except ValueError:
        await message.answer("❌ Некорректное значение. Введите положительное число:")
        return

    code = user_states.get(_uid(user_id, "promo_code"))
    dtype = user_states.get(_uid(user_id, "promo_type"), "percent")
    ok = await database.create_promocode(shop_id, code, dtype, value)

    if not ok:
        await message.answer(f"❌ Промокод {code} уже существует. Попробуйте другой код:")
        user_states[user_id] = UserState.SHOP_MENU
        return

    discount_str = f"-{int(value)}%" if dtype == "percent" else f"-{int(value)}₽"
    promos = await database.get_shop_promocodes(shop_id)
    await message.answer(
        f"✅ Промокод <b>{code}</b> ({discount_str}) создан!",
        reply_markup=keyboards.create_promocodes_menu(shop_id, promos)
    )
    user_states[user_id] = UserState.SHOP_MENU

    # Предупреждение о товарах, которые станут бесплатными
    rows = await database.get_shop_products_for_promo_check(shop_id)
    free_products = []
    for _, prod_name, price, sale_price in rows:
        effective = sale_price if (sale_price and 0 < sale_price < price) else price
        final = effective * (1 - value / 100) if dtype == "percent" else effective - value
        if final < 1.0:
            label = (f"{prod_name} ({price}₽ → {effective}₽ по акции)"
                     if effective != price else f"{prod_name} ({effective}₽)")
            free_products.append(label)
    if free_products:
        lines = "\n".join(f"• {l}" for l in free_products)
        await message.answer(
            f"⚠️ <b>Внимание!</b> С промокодом <b>{code}</b> ({discount_str}) "
            f"следующие товары станут <b>бесплатными</b> (итоговая цена &lt;1₽):\n\n"
            f"{lines}\n\nПокупатели смогут получить их без оплаты. "
            f"Если это не задумано — удалите промокод или поднимите цены."
        )

    # Авто-рассылка через бот магазина
    if shop_id in active_shop_bots:
        shop_info = await database.get_shop_info(shop_id)
        shop_name = shop_info[2] if shop_info else "магазин"
        users = await database.get_shop_user_ids(shop_id)
        if users:
            promo_msg = (
                f"🎉 Акция в магазине <b>{shop_name}</b>!\n\n"
                f"🎟️ Промокод: <b>{code}</b>\n"
                f"💸 Скидка: <b>{discount_str}</b>\n\n"
                "Введите промокод при оформлении заказа!"
            )
            shop_bot_inst = active_shop_bots[shop_id]

            async def _broadcast_promo():
                for uid in users:
                    try:
                        await shop_bot_inst.send_message(uid, promo_msg, parse_mode=ParseMode.HTML)
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass

            asyncio.create_task(_broadcast_promo())
            await message.answer(f"📢 Рассылка акции запущена для {len(users)} пользователей.")


# ── Универсальный текстовый обработчик ──

@dp.message()
async def text_handler(message: Message):
    user_id    = message.from_user.id
    user_state = user_states.get(user_id)

    # ─── Создание магазина ───
    if user_state == UserState.CREATING_SHOP:
        shop_name = message.text.strip() if message.text else ""
        if shop_name.lower() == 'назад':
            user_states[user_id] = UserState.MAIN_MENU
            await message.answer("❌ Создание магазина отменено", reply_markup=keyboards.create_main_menu())
            return
        if len(shop_name) < 2:
            await message.answer("❌ Название магазина должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
        shop_id = await database.create_shop(user_id, shop_name)
        if not shop_id:
            await message.answer("❌ Ошибка при создании магазина")
            return
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer(
            f"✅ Магазин '{shop_name}' успешно создан!\n\nТеперь настройте его:",
            reply_markup=keyboards.create_shop_management_menu(shop_id)
        )

    # ─── Токен бота ───
    elif user_state == UserState.EDITING_TOKEN:
        token   = message.text.strip() if message.text else ""
        shop_id = user_states.get(_uid(user_id, "shop_id"))
        if token.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            await message.answer("❌ Изменение токена отменено",
                                 reply_markup=keyboards.create_shop_management_menu(shop_id))
            return
        if len(token) < 30:
            await message.answer("❌ Токен должен содержать не менее 30 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
        bot_username = await database.update_shop_token(shop_id, token)
        user_states[user_id] = UserState.SHOP_MENU
        if bot_username:
            await message.answer("✅ Токен успешно обновлен!",
                                 reply_markup=keyboards.create_shop_management_menu(shop_id))
            shop_info = await database.get_shop_info(shop_id)
            welcome   = shop_info[5] if shop_info else "Добро пожаловать!"
            await _start_shop_bot_task(shop_id, token, welcome)
        else:
            await message.answer("❌ Неверный токен",
                                 reply_markup=keyboards.create_shop_management_menu(shop_id))

    # ─── Создание категории ───
    elif user_state == UserState.CREATING_CATEGORY:
        name    = message.text.strip() if message.text else ""
        shop_id = user_states.get(_uid(user_id, "shop_id"))
        if name.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            await message.answer("❌ Создание раздела отменено",
                                 reply_markup=keyboards.create_shop_management_menu(shop_id))
            return
        if len(name) < 2:
            await message.answer("❌ Название раздела должно содержать не менее 2 символов. Попробуйте снова или отправьте 'назад' для отмены")
            return
        category_id = await database.create_category(shop_id, name)
        if not category_id:
            await message.answer("❌ Ошибка при создании раздела")
            return
        await message.answer(f"✅ Раздел '{name}' создан!",
                             reply_markup=await keyboards.create_categories_menu(shop_id))
        user_states[user_id] = UserState.SHOP_MENU

    # ─── Редактирование названия категории ───
    elif user_state == UserState.EDITING_CATEGORY_NAME:
        new_name    = message.text.strip() if message.text else ""
        category_id = user_states.get(_uid(user_id, "category_id"))
        shop_id     = await database.get_shop_id_by_category(category_id)
        if new_name.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            await message.answer("❌ Изменение названия раздела отменено",
                                 reply_markup=await keyboards.create_categories_menu(shop_id))
            return
        if len(new_name) < 2:
            await message.answer("❌ Название раздела должно содержать не менее 2 символов.")
            return
        if await database.update_category_name(category_id, new_name):
            await message.answer(f"✅ Название раздела изменено на '{new_name}'!",
                                 reply_markup=await keyboards.create_categories_menu(shop_id))
            user_states[user_id] = UserState.SHOP_MENU
        else:
            await message.answer("❌ Ошибка при изменении названия раздела")

    # ─── Приветствие ───
    elif user_state == UserState.EDITING_WELCOME:
        welcome = message.text.strip() if message.text else ""
        shop_id = user_states.get(_uid(user_id, "shop_id"))
        if welcome.lower() == 'назад':
            user_states[user_id] = UserState.SHOP_MENU
            await message.answer("❌ Изменение приветствия отменено",
                                 reply_markup=keyboards.create_shop_management_menu(shop_id))
            return
        if len(welcome) < 5:
            await message.answer("❌ Сообщение слишком короткое (мин. 5 символов).")
            return
        if await database.update_welcome_message(shop_id, welcome):
            await message.answer("✅ Приветственное сообщение обновлено!",
                                 reply_markup=keyboards.create_shop_management_menu(shop_id))
            user_states[user_id] = UserState.SHOP_MENU
        else:
            await message.answer("❌ Ошибка при обновлении сообщения")

    # ─── Добавление товара: название ───
    elif user_state == UserState.PRODUCT_NAME:
        name = message.text.strip() if message.text else ""
        if name.lower() == 'назад':
            category_id = user_states.get(_uid(user_id, "category_id"))
            await message.answer("❌ Добавление товара отменено",
                                 reply_markup=await keyboards.create_products_menu(category_id))
            user_states[user_id] = UserState.SHOP_MENU
            return
        if len(name) < 2:
            await message.answer("❌ Название товара должно содержать не менее 2 символов.")
            return
        user_states[_uid(user_id, "product_name")] = name
        user_states[user_id] = UserState.PRODUCT_PRICE
        await message.answer("Введите цену товара (только положительное число):\n\nОтправьте 'назад' для отмены")

    # ─── Добавление товара: цена ───
    elif user_state == UserState.PRODUCT_PRICE:
        price_text = message.text.strip() if message.text else ""
        if price_text.lower() == 'назад':
            user_states[user_id] = UserState.PRODUCT_NAME
            cat_id = user_states.get(_uid(user_id, "category_id"))
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"category_{cat_id}"))
            await message.answer("❌ Ввод цены отменен\nВведите название товара:", reply_markup=builder.as_markup())
            return
        try:
            price = float(price_text)
            if price <= 0:
                raise ValueError()
            user_states[_uid(user_id, "product_price")] = price
            user_states[user_id] = UserState.PRODUCT_TYPE
            markup = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Цифровой"), KeyboardButton(text="Физический")]],
                one_time_keyboard=True, resize_keyboard=True
            )
            await message.answer("Выберите тип товара:", reply_markup=markup)
        except ValueError:
            await message.answer("❌ Некорректная цена. Введите положительное число")

    # ─── Добавление товара: тип ───
    elif user_state == UserState.PRODUCT_TYPE:
        product_type = message.text.strip().lower() if message.text else ""
        if product_type not in ['цифровой', 'физический']:
            await message.answer("Пожалуйста, выберите тип товара, используя кнопки.")
            return
        user_states[_uid(user_id, "product_is_digital")] = (product_type == 'цифровой')
        user_states[user_id] = UserState.PRODUCT_DESCRIPTION
        await message.answer(
            "Введите описание товара (или '-' чтобы пропустить):",
            reply_markup=ReplyKeyboardRemove()
        )

    # ─── Добавление товара: описание ───
    elif user_state == UserState.PRODUCT_DESCRIPTION:
        description = message.text.strip() if message.text else ""
        if description == '-':
            description = None
        user_states[_uid(user_id, "product_description")] = description
        user_states[user_id] = UserState.PRODUCT_IMAGE
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🖼️ Стандартное фото", callback_data="default_image"),
            InlineKeyboardButton(text="⏩ Пропустить", callback_data="skip_image"),
        )
        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_from_image"))
        await message.answer("Отправьте изображение товара или выберите опцию:", reply_markup=builder.as_markup())

    # ─── Добавление товара: фото (текст) ───
    elif user_state == UserState.PRODUCT_IMAGE:
        text = message.text.strip().lower() if message.text else ""
        category_id   = user_states.get(_uid(user_id, "category_id"))
        product_name  = user_states.get(_uid(user_id, "product_name"))
        product_price = user_states.get(_uid(user_id, "product_price"))
        description   = user_states.get(_uid(user_id, "product_description"))
        is_digital    = user_states.get(_uid(user_id, "product_is_digital"), True)

        if text == 'назад':
            user_states[user_id] = UserState.PRODUCT_DESCRIPTION
            await message.answer("❌ Добавление изображения отменено\nВведите описание товара:")
            return

        image_path = None
        if text == 'пропустить':
            image_path = None
        elif text == 'стандартное':
            image_path = "work_photos/default_not_image.jpg"
        else:
            await message.answer("❌ Некорректная опция. Отправьте фото, 'Пропустить', 'Стандартное' или 'назад'")
            return

        pid = await database.add_product(category_id, product_name, product_price,
                                         image_path, is_digital, description)
        if not pid:
            await message.answer("❌ Ошибка при добавлении товара")
            return
        await message.answer(f"✅ Товар '{product_name}' добавлен!")
        await message.answer("📦 Товары в разделе:",
                             reply_markup=await keyboards.create_products_menu(category_id))
        user_states[user_id] = UserState.SHOP_MENU
        _clear_product_state(user_id)

    # ─── Редактирование товара: текстовые поля ───
    elif user_state == UserState.EDITING_PRODUCT:
        edit_type   = user_states.get(_uid(user_id, "edit_type"))
        product_id  = user_states.get(_uid(user_id, "product_id"))
        category_id = user_states.get(_uid(user_id, "category_id"))
        page        = user_states.get(_uid(user_id, "page"), 0)

        if message.text and message.text.strip().lower() == 'назад':
            await message.answer("❌ Изменение товара отменено")
            await message.answer("📦 Товары в разделе:",
                                 reply_markup=await keyboards.create_products_menu(category_id, page))
            user_states[user_id] = UserState.SHOP_MENU
            return

        if not message.text:
            return

        text = message.text.strip()

        if edit_type == "name":
            if len(text) < 2:
                await message.answer("❌ Название товара должно содержать не менее 2 символов.")
                return
            await database.update_product(product_id, name=text)
            await message.answer("✅ Название товара обновлено!")
        elif edit_type == "price":
            try:
                new_price = float(text)
                if new_price <= 0:
                    raise ValueError()
                await database.update_product(product_id, price=new_price)
                await message.answer("✅ Цена товара обновлена!")
            except ValueError:
                await message.answer("❌ Некорректная цена. Введите положительное число или 'назад' для отмены")
                return
        elif edit_type == "desc":
            await database.update_product(product_id, description=text)
            await message.answer("✅ Описание товара обновлено!")
        elif edit_type == "photo":
            lw = text.lower()
            if lw == 'пропустить':
                await message.answer("✅ Изображение оставлено без изменений!")
            elif lw == 'стандартное':
                await database.update_product(product_id, image_path="work_photos/default_not_image.jpg")
                await message.answer("✅ Установлено стандартное изображение!")
            else:
                await message.answer("❌ Некорректная опция. Отправьте фото, 'Пропустить', 'Стандартное' или 'назад'")
                return
        elif edit_type == "sale":
            lw = text.lower()
            if lw in ("-", "убрать", "нет", "0"):
                await database.set_product_sale_price(product_id, None)
                await message.answer("✅ Акционная цена убрана!")
            else:
                try:
                    sale_price = float(text)
                    if sale_price <= 0:
                        raise ValueError()
                    product = await database.get_product_info(product_id)
                    if product and sale_price >= product[4]:
                        await message.answer(f"❌ Акционная цена должна быть меньше обычной ({product[4]}₽).")
                        return
                    await database.set_product_sale_price(product_id, sale_price)
                    await message.answer(f"✅ Акционная цена {sale_price}₽ установлена!")
                except ValueError:
                    await message.answer("❌ Введите число (цену) или '-' чтобы убрать скидку:")
                    return

        await message.answer("📦 Товары в разделе:",
                             reply_markup=await keyboards.create_products_menu(category_id, page))
        user_states[user_id] = UserState.SHOP_MENU
    else:
        await message.answer("Используйте кнопки меню для навигации:", reply_markup=keyboards.create_main_menu())


# ─── Фото: загрузка товара ───

@dp.message(F.photo,
            F.func(lambda m: user_states.get(m.from_user.id) == UserState.PRODUCT_IMAGE))
async def handle_product_image_photo(message: Message):
    user_id = message.from_user.id
    if message.caption and message.caption.strip().lower() == 'назад':
        user_states[user_id] = UserState.PRODUCT_DESCRIPTION
        await message.answer("❌ Добавление фото отменено\nВведите описание товара:")
        return

    os.makedirs("product_images", exist_ok=True)
    file_id   = message.photo[-1].file_id
    file_info = await bot.get_file(file_id)
    file_data = await bot.download_file(file_info.file_path)
    image_path = f"product_images/{uuid.uuid4().hex}.jpg"
    with open(image_path, 'wb') as f:
        f.write(file_data.read())

    category_id   = user_states.get(_uid(user_id, "category_id"))
    product_name  = user_states.get(_uid(user_id, "product_name"))
    product_price = user_states.get(_uid(user_id, "product_price"))
    description   = user_states.get(_uid(user_id, "product_description"))
    is_digital    = user_states.get(_uid(user_id, "product_is_digital"), True)

    pid = await database.add_product(category_id, product_name, product_price,
                                     image_path, is_digital, description)
    if not pid:
        await message.answer("❌ Ошибка при добавлении товара")
        return
    await message.answer(f"✅ Товар '{product_name}' добавлен!")
    await message.answer("📦 Товары в разделе:",
                         reply_markup=await keyboards.create_products_menu(category_id))
    user_states[user_id] = UserState.SHOP_MENU
    _clear_product_state(user_id)


# ─── Фото: редактирование товара ───

@dp.message(F.photo,
            F.func(lambda m: user_states.get(m.from_user.id) == UserState.EDITING_PRODUCT
                             and user_states.get(f"{m.from_user.id}_edit_type") == 'photo'))
async def handle_edit_product_photo(message: Message):
    user_id    = message.from_user.id
    product_id = user_states.get(_uid(user_id, "product_id"))
    category_id = user_states.get(_uid(user_id, "category_id"))
    page       = user_states.get(_uid(user_id, "page"), 0)

    product = await database.get_product_info(product_id)
    if not product:
        await message.answer("❌ Товар не найден")
        return

    old_image_path = product[5]
    os.makedirs("product_images", exist_ok=True)
    file_id   = message.photo[-1].file_id
    file_info = await bot.get_file(file_id)
    file_data = await bot.download_file(file_info.file_path)
    new_path  = f"product_images/{uuid.uuid4().hex}.jpg"
    with open(new_path, 'wb') as f:
        f.write(file_data.read())

    await database.update_product(product_id, image_path=new_path)

    if old_image_path and os.path.exists(old_image_path) and "default_not_image" not in old_image_path:
        try:
            os.remove(old_image_path)
        except Exception as e:
            logger.error(f"Ошибка удаления старого изображения: {e}")

    await message.answer("✅ Фото товара обновлено!")
    await message.answer("📦 Товары в разделе:",
                         reply_markup=await keyboards.create_products_menu(category_id, page))
    user_states[user_id] = UserState.SHOP_MENU


# ──────────────────────────────────────────────────────────────────────────────
#  Точка входа
# ──────────────────────────────────────────────────────────────────────────────

async def main():
    print("Инициализация базы данных...")
    database.init_database()
    print("База данных готова!")

    # Запускаем боты магазинов, у которых уже есть токен
    import aiosqlite
    async with aiosqlite.connect(database.DB_NAME) as db:
        async with db.execute(
            "SELECT id, bot_token, welcome_message FROM shops WHERE bot_token IS NOT NULL AND is_running=1"
        ) as cur:
            shops = await cur.fetchall()

    for shop_id, token, welcome_message in shops:
        await _start_shop_bot_task(shop_id, token, welcome_message or "Добро пожаловать!")
        print(f"  → Запущен бот магазина #{shop_id}")

    print(f"Бот-менеджер запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())