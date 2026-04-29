"""
main.py  —  бот-менеджер (aiogram 3.x)

Запускает менеджер-бот + по одному asyncio-Task на каждый магазин.
"""

import asyncio
import json
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
    InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database
import digital_delivery
import keyboards
import legal
import shop_bot as shop_bot_module
from states import UserState

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.ERROR))
logger = logging.getLogger(__name__)

# ─── Глобальное состояние ───
user_states: dict   = {}          # user_id → UserState str  |  "uid_key" → value
active_shop_bots: dict = {}       # shop_id → Bot instance
shop_tasks: dict    = {}          # shop_id → asyncio.Task

config.assert_bot_token()
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()


async def _ensure_terms(user_id: int, username: Optional[str], chat_id: int) -> bool:
    """Если пользователь не принял актуальные правила — показывает экран принятия и возвращает False."""
    await database.add_user(user_id, username)
    if await database.has_accepted_terms(user_id, legal.TERMS_VERSION):
        return True
    text = (
        "👋 Для продолжения нужно принять Правила пользования платформой.\n\n"
        f"{legal.DISCLAIMER_SHORT}"
    )
    await bot.send_message(chat_id, text, reply_markup=keyboards.create_terms_acceptance_menu())
    return False


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

WELCOME_TEXT = (
    "🛍️ Добро пожаловать в Shop Manager Bot!\n\n"
    "Этот бот поможет вам создать и управлять собственными магазинами в Telegram.\n\n"
    "<b>Как запустить свой магазин за 3 шага:</b>\n"
    "1️⃣ Создайте магазин в этом боте — кнопка «🏪 Мои магазины».\n"
    "2️⃣ Получите токен бота-витрины через @BotFather и вставьте его "
    "в разделе «🔑 API токен» вашего магазина.\n"
    "3️⃣ Добавьте категории, товары (можно цифровые — будут отправляться "
    "покупателю автоматически после оплаты) и пригласите работников.\n\n"
    "<b>Платежи:</b>\n"
    "• <b>Онлайн через платформу</b> — деньги зачисляются на ваш внутренний "
    "баланс, выводятся через раздел «💰 Финансы». Подключать собственный "
    "платёжный токен не нужно.\n"
    "• <b>Оплата при получении</b> — расчёты напрямую между вами и покупателем.\n\n"
    "Команды: /terms — правила, /legal — запрещённые товары, "
    "/moderation — модерация (для админов).\n\n"
    "Выберите действие:"
)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    # Deeplink распарсиваем ДО проверки правил, чтобы при первом заходе
    # покупатель не терял намерение «оплатить корзину/заказ» после принятия
    # правил. Сохраняем deeplink в user_states и обрабатываем его сразу
    # после accept_terms.
    raw = (message.text or "")
    parts = raw.split(maxsplit=1)
    pending_deeplink: Optional[str] = None
    if len(parts) == 2:
        arg = parts[1]
        if arg.startswith("pay_g_") and arg[len("pay_g_"):]:
            pending_deeplink = arg
        elif arg.startswith("pay_"):
            try:
                int(arg.split("_", 1)[1])
                pending_deeplink = arg
            except (ValueError, IndexError):
                pending_deeplink = None
    if pending_deeplink:
        user_states[_uid(user_id, "pending_pay_arg")] = pending_deeplink
    if not await _ensure_terms(user_id, message.from_user.username, message.chat.id):
        return
    # Правила приняты — обрабатываем deeplink (если был) и выходим.
    if pending_deeplink:
        user_states.pop(_uid(user_id, "pending_pay_arg"), None)
        await _handle_pay_deeplink(message.chat.id, user_id, pending_deeplink)
        return
    user_states[user_id] = UserState.MAIN_MENU
    await message.answer(WELCOME_TEXT, reply_markup=keyboards.create_main_menu())


async def _handle_pay_deeplink(chat_id: int, user_id: int, arg: str) -> None:
    """Маршрутизирует pay_*-аргумент `/start` в нужный invoice-генератор."""
    if arg.startswith("pay_g_"):
        group_id = arg[len("pay_g_"):]
        if group_id:
            await _send_payment_invoice_group(chat_id, user_id, group_id)
            return
    if arg.startswith("pay_"):
        try:
            order_id = int(arg.split("_", 1)[1])
        except (ValueError, IndexError):
            return
        if order_id:
            await _send_payment_invoice(chat_id, user_id, order_id)


async def _send_payment_invoice(chat_id: int, user_id: int, order_id: int) -> None:
    """Отправляет invoice через PAYMENTS_TOKEN для заказа из магазин-бота."""
    order = await database.get_order(order_id)
    if not order:
        await bot.send_message(chat_id, "❌ Заказ не найден.")
        return
    if order["customer_user_id"] != user_id:
        await bot.send_message(chat_id, "❌ Этот заказ оформлен другим пользователем.")
        return
    if order["status"] != database.ORDER_STATUS_NEW:
        await bot.send_message(
            chat_id,
            f"ℹ️ Заказ #{order_id} уже в статусе «{database.ORDER_STATUS_LABELS.get(order['status'], order['status'])}». "
            f"Повторная оплата не нужна."
        )
        return
    if not config.PAYMENTS_TOKEN:
        await bot.send_message(chat_id, "❌ Онлайн-оплата временно недоступна. Сообщите продавцу.")
        return
    total = float(order["total_price"] or 0)
    if total <= 0:
        await bot.send_message(chat_id, "❌ Некорректная сумма заказа.")
        return
    price_kopecks = int(round(total * 100))
    # Telegram Payments требует минимум 1 RUB и максимум ~99999 RUB.
    # Раньше мы клампили снизу до 100 копеек, но это рассинхронизировалось
    # с manager_pre_checkout (он сравнивает с total как есть) и вело к
    # PAYMENT_AMOUNT_INVALID на копеечных строках корзины. Теперь просто
    # отказываем в оплате через Telegram, если сумма меньше минимума.
    if price_kopecks < 100:
        await bot.send_message(
            chat_id,
            "❌ Сумма заказа меньше минимально допустимой для онлайн-оплаты "
            "(1 ₽). Пожалуйста, обратитесь к продавцу."
        )
        return
    # Раньше при price_kopecks > 9_999_900 мы молча клампили сверху, но
    # manager_pre_checkout сравнивает с total_price из БД без clamp →
    # покупатель видел invoice, который заведомо не сможет оплатить
    # ("Сумма не совпадает с заказом"). Корректнее сразу отказать с
    # понятным сообщением и не отправлять заведомо нерабочий счёт.
    if price_kopecks > 9_999_900:
        await bot.send_message(
            chat_id,
            "❌ Сумма заказа превышает максимально допустимую для онлайн-оплаты "
            "(99 999 ₽). Разделите покупку или обратитесь к продавцу."
        )
        return
    title = (order.get("product_name") or "Заказ")[:32]
    desc = (
        f"Заказ #{order_id} в магазине «{order.get('shop_name') or '—'}»\n"
        f"{order.get('product_name') or ''} ×{order['quantity']}"
    )[:255]
    payload = f"order_{order_id}"
    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=desc,
            payload=payload,
            provider_token=config.PAYMENTS_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=title, amount=price_kopecks)],
            start_parameter=f"pay_{order_id}",
        )
    except Exception as e:
        logger.error(f"send_invoice failed for order {order_id}: {e}")
        await bot.send_message(
            chat_id,
            "❌ Не удалось создать счёт на оплату. Попробуйте позже или свяжитесь с продавцом."
        )


async def _send_payment_invoice_group(chat_id: int, user_id: int, group_id: str) -> None:
    """Один invoice на всю корзину (order_group_id).

    Все NEW-заказы в группе, принадлежащие пользователю, складываются в один
    счёт со списком позиций (LabeledPrice на каждый order). После оплаты
    manager_successful_payment переведёт всю группу в PAID и зачислит баланс.
    """
    orders = await database.get_orders_by_group(group_id)
    if not orders:
        await bot.send_message(chat_id, "❌ Корзина не найдена.")
        return
    # Только NEW-заказы данного пользователя; остальные игнорируем.
    pending = [o for o in orders
               if o["customer_user_id"] == user_id
               and o["status"] == database.ORDER_STATUS_NEW]
    if not pending:
        # Заказы либо уже оплачены, либо принадлежат другому пользователю.
        if any(o["customer_user_id"] != user_id for o in orders):
            await bot.send_message(chat_id, "❌ Эта корзина оформлена другим пользователем.")
            return
        await bot.send_message(
            chat_id,
            f"ℹ️ Корзина уже обработана (заказов в статусе «новый» нет). "
            f"Откройте «📋 Мои заказы» в магазине для проверки статусов."
        )
        return
    if not config.PAYMENTS_TOKEN:
        await bot.send_message(chat_id, "❌ Онлайн-оплата временно недоступна. Сообщите продавцу.")
        return
    total_kopecks = 0
    prices: list[LabeledPrice] = []
    for o in pending:
        line_kop = int(round(float(o["total_price"] or 0) * 100))
        if line_kop <= 0:
            continue
        total_kopecks += line_kop
        # Telegram ограничивает label до 32 символов.
        label = (f"#{o['id']} {o.get('product_name') or 'Товар'} ×{o['quantity']}")[:32]
        prices.append(LabeledPrice(label=label, amount=line_kop))
    if not prices or total_kopecks <= 0:
        await bot.send_message(chat_id, "❌ Сумма корзины некорректна.")
        return
    if total_kopecks < 100:
        await bot.send_message(
            chat_id,
            "❌ Сумма корзины меньше минимально допустимой для онлайн-оплаты "
            "(1 ₽). Пожалуйста, обратитесь к продавцу."
        )
        return
    if total_kopecks > 9_999_900:
        await bot.send_message(
            chat_id,
            "❌ Сумма корзины превышает максимально допустимую для онлайн-оплаты "
            "(99 999 ₽). Разделите покупку или обратитесь к продавцу."
        )
        return
    shop_name = pending[0].get("shop_name") or "—"
    title = f"Корзина в «{shop_name}»"[:32]
    desc = (f"Оплата {len(pending)} "
            f"{'позиции' if 1 < len(pending) < 5 else ('позиция' if len(pending) == 1 else 'позиций')} "
            f"в магазине «{shop_name}»")[:255]
    payload = f"group_{group_id}"
    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=desc,
            payload=payload,
            provider_token=config.PAYMENTS_TOKEN,
            currency="RUB",
            prices=prices,
            start_parameter=f"pay_g_{group_id}",
        )
    except Exception as e:
        logger.error(f"send_invoice failed for group {group_id}: {e}")
        await bot.send_message(
            chat_id,
            "❌ Не удалось создать счёт на оплату корзины. Попробуйте позже или свяжитесь с продавцом."
        )


@dp.message(Command("terms"))
async def cmd_terms(message: Message):
    accepted = await database.has_accepted_terms(message.from_user.id, legal.TERMS_VERSION)
    suffix = "\n\n✅ Вы уже приняли эти правила." if accepted else ""
    await message.answer(legal.TERMS_OF_USE + suffix,
                         reply_markup=keyboards.create_terms_acceptance_menu()
                         if not accepted else None)


@dp.message(Command("legal"))
async def cmd_legal(message: Message):
    await message.answer(legal.PROHIBITED_GOODS)


@dp.message(Command("moderation"))
async def cmd_moderation(message: Message):
    if not config.is_moderator(message.from_user.id):
        await message.answer("⛔ У вас нет прав модератора.")
        return
    await message.answer(
        "🛡️ <b>Панель модерации</b>\n\nВыберите раздел:",
        reply_markup=keyboards.create_moderation_main_menu()
    )


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
    # Кнопки в чате, у которых в callback_data попало литеральное "None"
    # (баг старых рендеров меню до фикса EDITING_PRODUCT) при клике падали
    # с `invalid literal for int() with base 10: 'None'`. Перехватываем
    # такие кнопки и показываем понятное сообщение, чтобы пользователь
    # не получал «Произошла ошибка. Попробуйте снова.» вечно.
    if data and "_None_" in f"_{data}_":
        await call.answer(
            "Эта кнопка устарела. Откройте раздел заново из главного меню.",
            show_alert=True
        )
        return
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
            # Никогда не показываем токен полностью — только маску.
            cur_token = config.mask_secret(shop_info[3]) if shop_info and shop_info[3] else "Не установлен"
            user_states[user_id]                  = UserState.EDITING_TOKEN
            user_states[_uid(user_id, "shop_id")] = shop_id
            await call.message.edit_text(
                f"🔑 Токен API бота\n\nТекущий токен: <code>{cur_token}</code>\n\n"
                f"Введите новый токен (минимум 30 символов):\n\n"
                f"⚠️ Токен — секрет. Не пересылайте его в чаты. После сохранения "
                f"в боте будет отображаться только маска.\n\n"
                f"Отправьте 'назад' для отмены",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
            )

        # ── PayMaster (устарело: платежи теперь идут через PAYMENTS_TOKEN платформы) ──
        elif data.startswith("paymaster_token_"):
            shop_id = int(data.split("_")[-1])
            await call.message.edit_text(
                "ℹ️ Отдельная настройка PayMaster для каждого магазина больше не нужна.\n\n"
                "Все платежи покупателей теперь идут через единый PayMaster платформы. "
                "Деньги зачисляются на ваш внутренний баланс — выводите их в разделе "
                "<b>«💰 Финансы»</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.create_back_button_menu(f"manage_shop_{shop_id}")
            )

        # ── Финансы магазина (баланс + вывод) ──
        elif data.startswith("finance_") and not data.startswith("finance_history_"):
            shop_id = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден", show_alert=True)
                return
            # Доступ только владельцу/админу магазина или OWNER_IDS.
            admins = await database.get_shop_admins_ids(shop_id)
            if user_id != shop_info[1] and user_id not in admins and not config.is_owner(user_id):
                await call.answer("Нет доступа", show_alert=True)
                return
            bal = await database.get_seller_balance(shop_id)
            shop_payment_method = shop_info[4] if len(shop_info) > 4 else None
            text = (
                f"💰 <b>Финансы — {shop_info[2]}</b>\n\n"
                f"Доступно к выводу: <b>{bal['amount_rub']:.2f} ₽</b>\n"
                f"Всего заработано: {bal['total_earned_rub']:.2f} ₽\n\n"
                f"Минимальная сумма вывода: {config.MIN_WITHDRAWAL} ₽\n\n"
                f"Платежи покупателей принимаются единым PayMaster платформы; "
                f"фактическую выплату делает владелец платформы после вашего запроса."
            )
            # Подсказка: если магазин стоит на «оплате при получении», деньги
            # никогда не пройдут через платформу и баланс будет всегда 0.
            # Если продавец зашёл сюда с пустым балансом — намекаем, как это
            # изменить.
            if (bal['amount_rub'] == 0 and bal['total_earned_rub'] == 0
                    and shop_payment_method == 'cash_on_delivery'):
                text += (
                    "\n\n💡 <b>Почему здесь 0 ₽?</b>\n"
                    "Сейчас в магазине включён способ <b>«Оплата при получении»</b> — "
                    "деньги идут напрямую от покупателя продавцу и через платформу не "
                    "проходят. Чтобы накапливать баланс и выводить его через платформу, "
                    "переключите способ оплаты на <b>«Онлайн через платформу»</b> в "
                    "разделе «💳 Способ оплаты»."
                )
            await call.message.edit_text(text, parse_mode=ParseMode.HTML,
                                         reply_markup=keyboards.create_finance_menu(shop_id))

        # ── Запросить вывод: выбор способа ──
        elif data.startswith("withdraw_start_"):
            shop_id = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info or shop_info[1] != user_id:
                await call.answer("Только владелец магазина может запросить вывод", show_alert=True)
                return
            bal = await database.get_seller_balance(shop_id)
            if bal["amount_rub"] < config.MIN_WITHDRAWAL:
                await call.answer(
                    f"Минимум для вывода — {config.MIN_WITHDRAWAL} ₽. "
                    f"На балансе: {bal['amount_rub']:.2f} ₽",
                    show_alert=True
                )
                return
            await call.message.edit_text(
                f"💸 <b>Запрос на вывод</b>\n\nДоступно: {bal['amount_rub']:.2f} ₽\n\n"
                f"Выберите способ получения:",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.create_withdraw_method_menu(shop_id)
            )

        # ── Выбран способ → вводим реквизиты ──
        elif data.startswith("withdraw_method_"):
            parts = data.split("_")
            shop_id = int(parts[2])
            method  = parts[3]
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info or shop_info[1] != user_id:
                await call.answer("Нет доступа", show_alert=True)
                return
            if method not in database.WITHDRAWAL_METHODS:
                await call.answer("Неизвестный способ", show_alert=True)
                return
            bal = await database.get_seller_balance(shop_id)
            user_states[user_id] = UserState.WITHDRAW_AMOUNT
            user_states[_uid(user_id, "shop_id")] = shop_id
            user_states[_uid(user_id, "withdraw_method")] = method
            await call.message.edit_text(
                f"Способ: {database.WITHDRAWAL_METHOD_LABELS[method]}\n\n"
                f"Доступно: <b>{bal['amount_rub']:.2f} ₽</b>\n"
                f"Минимум: {config.MIN_WITHDRAWAL} ₽\n\n"
                f"Введите сумму к выводу в рублях (целое число или с запятой), "
                f"или отправьте 'назад' для отмены.",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.create_back_button_menu(f"finance_{shop_id}")
            )

        # ── История выводов ──
        elif data.startswith("withdraw_history_"):
            shop_id = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            admins = await database.get_shop_admins_ids(shop_id)
            if not shop_info or (user_id != shop_info[1] and user_id not in admins
                                  and not config.is_owner(user_id)):
                await call.answer("Нет доступа", show_alert=True)
                return
            rows = await database.list_shop_withdrawals(shop_id, limit=10)
            if not rows:
                text = "📜 История выводов пуста."
            else:
                lines = ["📜 <b>Последние выводы:</b>\n"]
                for r in rows:
                    label = database.WITHDRAWAL_STATUS_LABELS.get(r["status"], r["status"])
                    method_label = database.WITHDRAWAL_METHOD_LABELS.get(r["method"], r["method"])
                    lines.append(
                        f"• #{r['id']} · {r['amount_rub']:.2f} ₽ · {method_label}\n"
                        f"  {label} · {r['created_at']}"
                    )
                text = "\n".join(lines)
            await call.message.edit_text(text, parse_mode=ParseMode.HTML,
                                         reply_markup=keyboards.create_back_button_menu(f"finance_{shop_id}"))

        # ── Владелец платформы: отметить выплачено / отклонить ──
        elif data.startswith("withdraw_paid_"):
            wid = int(data.split("_")[-1])
            if not config.is_owner(user_id):
                await call.answer("Только владелец платформы", show_alert=True)
                return
            ok = await database.mark_withdrawal_paid_out(wid)
            if not ok:
                await call.answer("Не удалось отметить (вывод уже обработан?)", show_alert=True)
                return
            w = await database.get_withdrawal(wid)
            await call.answer("Отмечено как выплачено")
            try:
                await call.message.edit_text(
                    (call.message.html_text or call.message.text or "")
                    + "\n\n✅ Отмечено как выплачено.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            # Уведомим продавца
            if w:
                try:
                    await bot.send_message(
                        w["seller_user_id"],
                        f"✅ Вывод #{w['id']} на сумму {w['amount_rub']:.2f} ₽ выплачен."
                    )
                except Exception:
                    pass

        elif data.startswith("withdraw_reject_"):
            wid = int(data.split("_")[-1])
            if not config.is_owner(user_id):
                await call.answer("Только владелец платформы", show_alert=True)
                return
            user_states[user_id] = UserState.WITHDRAW_REJECT_NOTE
            user_states[_uid(user_id, "withdrawal_id")] = wid
            await call.message.answer(
                "Введите причину отказа (она будет показана продавцу). "
                "Сумма вывода вернётся ему на баланс."
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
        # Поддерживаются: онлайн через единый платёжный шлюз платформы и
        # оплата при получении (cash_on_delivery). Отдельная настройка
        # ЮKassa у магазина больше не нужна — токен платформы лежит в .env.
        elif data.startswith("payment_method_"):
            shop_id = int(data.split("_")[-1])
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(
                text="💳 Онлайн через платформу",
                callback_data=f"set_payment_online_{shop_id}"
            ))
            builder.row(InlineKeyboardButton(
                text="💵 Оплата при получении",
                callback_data=f"set_payment_cash_{shop_id}"
            ))
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
            await call.message.edit_text(
                "Выберите способ оплаты, который будет доступен покупателям в этом магазине:",
                reply_markup=builder.as_markup()
            )

        elif data.startswith("set_payment_"):
            shop_id      = int(data.split("_")[-1])
            payment_type = "cash_on_delivery" if "cash" in data else "online"
            await database.update_payment_method(shop_id, payment_type)
            label = ("💵 Оплата при получении"
                     if payment_type == "cash_on_delivery"
                     else "💳 Онлайн через платформу")
            extra = ("\n\nДеньги будут списываться у покупателя в менеджер-боте платформы "
                     "и зачисляться на ваш внутренний баланс. Выводите их в разделе "
                     "«💰 Финансы»."
                     if payment_type == "online" else
                     "\n\nДеньги вы получаете напрямую при передаче товара покупателю. "
                     "Платформа в этом случае не участвует в расчётах.")
            await call.message.edit_text(
                f"✅ Способ оплаты установлен: {label}.{extra}",
                reply_markup=keyboards.create_shop_management_menu(shop_id)
            )

        # ── Удаление магазина: шаг 1 (подтверждение) ──
        elif data.startswith("delete_shop_"):
            shop_id   = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден")
                return
            if shop_info[1] != user_id:
                await call.answer("Только создатель магазина может его удалить")
                return
            confirm_kb = InlineKeyboardBuilder()
            confirm_kb.row(InlineKeyboardButton(
                text="🗑 Да, удалить безвозвратно",
                callback_data=f"do_delete_shop_{shop_id}"
            ))
            confirm_kb.row(InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"manage_shop_{shop_id}"
            ))
            await call.message.edit_text(
                f"⚠️ <b>Удалить магазин «{shop_info[2]}»?</b>\n\n"
                "Это действие <b>необратимо</b>. Будут удалены:\n"
                "• сам магазин и его настройки;\n"
                "• все товары и категории магазина;\n"
                "• история заказов, отзывов и споров;\n"
                "• токен бота-витрины.\n\n"
                "Вывод средств с уже накопленного баланса делайте до удаления.",
                parse_mode=ParseMode.HTML,
                reply_markup=confirm_kb.as_markup()
            )

        # ── Удаление магазина: шаг 2 (фактическое удаление) ──
        elif data.startswith("do_delete_shop_"):
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
            user_states[user_id]                      = UserState.EDITING_PRODUCT
            # Раньше тут НЕ записывались product_id/category_id/page в user_states.
            # Из-за этого, если пользователь после открытия товара отправлял
            # любой текст, ветка `EDITING_PRODUCT` в handle_messages читала None
            # и строила меню с callback_data вида `digital_menu_None_None_0`,
            # который при следующем клике падал с
            # `invalid literal for int() with base 10: 'None'`.
            user_states[_uid(user_id, "product_id")]  = product_id
            user_states[_uid(user_id, "category_id")] = category_id
            user_states[_uid(user_id, "page")]        = page
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

        # ── Удаление товара: шаг 1 (подтверждение) ──
        elif data.startswith("delete_product_"):
            parts = data.split("_")
            if len(parts) < 5:
                await call.answer("Неверные данные")
                return
            product_id  = int(parts[2])
            category_id = int(parts[3])
            page        = int(parts[4])
            product = await database.get_product_info(product_id)
            product_name = product[2] if product and len(product) > 2 else f"#{product_id}"
            confirm_kb = InlineKeyboardBuilder()
            confirm_kb.row(InlineKeyboardButton(
                text="🗑 Да, удалить безвозвратно",
                callback_data=f"do_delete_product_{product_id}_{category_id}_{page}"
            ))
            confirm_kb.row(InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"product_{product_id}_{category_id}_{page}"
            ))
            await call.message.edit_text(
                f"⚠️ <b>Удалить товар «{product_name}»?</b>\n\n"
                "Это действие <b>необратимо</b>: сам товар, его фото и "
                "цифровой контент будут удалены.\n"
                "На уже оформленные заказы это не повлияет.",
                parse_mode=ParseMode.HTML,
                reply_markup=confirm_kb.as_markup()
            )

        # ── Удаление товара: шаг 2 (фактическое удаление) ──
        elif data.startswith("do_delete_product_"):
            parts = data.split("_")
            if len(parts) < 6:
                await call.answer("Неверные данные")
                return
            product_id  = int(parts[3])
            category_id = int(parts[4])
            page        = int(parts[5])
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

        # ── Удаление категории: шаг 1 (подтверждение) ──
        elif data.startswith("delete_category_"):
            category_id = int(data.split("_")[-1])
            shop_id     = await database.get_shop_id_by_category(category_id)
            confirm_kb = InlineKeyboardBuilder()
            confirm_kb.row(InlineKeyboardButton(
                text="🗑 Да, удалить безвозвратно",
                callback_data=f"do_delete_category_{category_id}"
            ))
            confirm_kb.row(InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"category_{category_id}"
            ))
            await call.message.edit_text(
                "⚠️ <b>Удалить раздел вместе со всеми товарами в нём?</b>\n\n"
                "Это действие <b>необратимо</b>: будут удалены все товары "
                "категории, их фотографии и цифровой контент.\n"
                "На уже оформленные заказы это не повлияет.",
                parse_mode=ParseMode.HTML,
                reply_markup=confirm_kb.as_markup()
            )

        # ── Удаление категории: шаг 2 (фактическое удаление) ──
        elif data.startswith("do_delete_category_"):
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
            order_id = int(data.split("_")[-1])
            order = await database.get_order(order_id)
            if not order:
                await call.answer("Заказ не найден")
                return
            text = _format_order_for_admin(order)
            await call.message.edit_text(
                text, reply_markup=keyboards.create_order_admin_menu(order),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("adm_order_set_"):
            parts = data.split("_")
            order_id   = int(parts[3])
            new_status = parts[4]
            order = await database.get_order(order_id)
            if not order:
                await call.answer("Заказ не найден")
                return
            ok = await database.update_order_status(order_id, new_status)
            if not ok:
                await call.answer("Неверный статус")
                return
            await call.answer("✅ Статус обновлён")
            await _notify_customer_status_change(order_id, new_status)
            order = await database.get_order(order_id)
            await call.message.edit_text(
                _format_order_for_admin(order),
                reply_markup=keyboards.create_order_admin_menu(order),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("adm_order_deliver_"):
            order_id = int(data.split("_")[-1])
            order = await database.get_order(order_id)
            if not order:
                await call.answer("Заказ не найден")
                return
            sent = await _deliver_digital_content(order)
            if sent:
                await call.answer("✅ Отправлено покупателю")
            else:
                await call.answer("⚠️ Цифровой контент не настроен у товара. Настройте в карточке товара.")
            order = await database.get_order(order_id)
            await call.message.edit_text(
                _format_order_for_admin(order),
                reply_markup=keyboards.create_order_admin_menu(order),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("adm_order_msg_"):
            order_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.REPLYING_DISPUTE
            user_states[_uid(user_id, "reply_order_id")] = order_id
            user_states[_uid(user_id, "reply_kind")] = "order"
            await call.message.edit_text(
                "Введите сообщение для покупателя по заказу:\n\nОтправьте «назад» для отмены.",
                reply_markup=keyboards.create_back_button_menu(f"order_detail_{order_id}")
            )

        elif data.startswith("adm_dispute_open_"):
            order_id = int(data.split("_")[-1])
            user_states[user_id] = UserState.REPLYING_DISPUTE
            user_states[_uid(user_id, "reply_order_id")] = order_id
            user_states[_uid(user_id, "reply_kind")] = "dispute_seller"
            await call.message.edit_text(
                "Вы открываете спор по заказу. Опишите причину:\n\nОтправьте «назад» для отмены.",
                reply_markup=keyboards.create_back_button_menu(f"order_detail_{order_id}")
            )

        # ── Редактирование цифрового контента товара ──
        elif data.startswith("digital_menu_"):
            parts = data.split("_")
            product_id  = int(parts[2])
            category_id = int(parts[3])
            page        = int(parts[4])
            info = await database.get_product_digital(product_id) or {}
            kind = info.get("kind") or "—"
            content = info.get("content") or ""
            ttl = info.get("ttl_hours")
            if kind == "bundle":
                items = digital_delivery.parse_bundle(content)
                preview = f"пакет из {len(items)} элементов"
            elif content:
                preview = content if isinstance(content, str) and len(content) < 120 else (str(content)[:117] + "...")
            else:
                preview = "не задано"
            text = (
                f"💾 <b>Цифровой контент</b>\n\n"
                f"Тип: <code>{kind}</code>\n"
                f"Содержимое: {preview}\n"
                f"Срок действия: {f'{ttl} ч' if ttl else 'не задан'}\n\n"
                "Контент будет автоматически отправлен покупателю после оплаты."
            )
            await call.message.edit_text(
                text, reply_markup=keyboards.create_digital_content_menu(product_id, category_id, page),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("edit_digital_"):
            parts = data.split("_")
            product_id  = int(parts[2])
            category_id = int(parts[3]) if len(parts) > 3 else 0
            page        = int(parts[4]) if len(parts) > 4 else 0
            user_states[user_id] = UserState.EDITING_DIGITAL_CONTENT
            user_states[_uid(user_id, "product_id")] = product_id
            user_states[_uid(user_id, "category_id")] = category_id
            user_states[_uid(user_id, "page")] = page
            user_states[_uid(user_id, "digital_bundle")] = []
            await call.message.edit_text(
                _format_bundle_status(0) + "\n\n" + _BUNDLE_PROMPT,
                reply_markup=_bundle_edit_kb(product_id, category_id, page, count=0),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("dbundle_save_"):
            parts = data.split("_")
            product_id  = int(parts[2])
            category_id = int(parts[3]) if len(parts) > 3 else 0
            page        = int(parts[4]) if len(parts) > 4 else 0
            items = user_states.get(_uid(user_id, "digital_bundle")) or []
            if not items:
                await call.answer("⚠️ Пакет пуст. Отправьте хотя бы один элемент.", show_alert=True)
                return
            existing = await database.get_product_digital(product_id) or {}
            ttl = existing.get("ttl_hours")
            if len(items) == 1:
                # Не плодим bundle ради одного элемента — храним как одиночный.
                only = items[0]
                kind, content = only["kind"], only["content"]
                # Подпись (caption) у одиночного фото/видео/файла теряется при
                # текущей схеме — но в одиночном режиме это и не редактировалось.
            else:
                kind = "bundle"
                content = digital_delivery.serialize_bundle(items)
            await database.update_product_digital(product_id, kind, content, ttl)
            user_states[_uid(user_id, "digital_bundle")] = []
            user_states[user_id] = UserState.EDITING_PRODUCT
            digital = await database.get_product_digital(product_id) or {}
            kind_lbl = "пакет" if digital.get("kind") == "bundle" else (digital.get("kind") or "—")
            info_text = (
                f"✅ Цифровой контент сохранён ({len(items)} эл.).\n\n"
                "💾 <b>Цифровой контент</b>\n"
                f"Тип: {kind_lbl}\n"
                f"Срок (ч): {digital.get('ttl_hours') if digital.get('ttl_hours') else '—'}"
            )
            await call.message.edit_text(
                info_text,
                reply_markup=keyboards.create_digital_content_menu(product_id, category_id, page),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("dbundle_reset_"):
            parts = data.split("_")
            product_id  = int(parts[2])
            category_id = int(parts[3]) if len(parts) > 3 else 0
            page        = int(parts[4]) if len(parts) > 4 else 0
            user_states[_uid(user_id, "digital_bundle")] = []
            await call.answer("🗑 Пакет очищен")
            await call.message.edit_text(
                _format_bundle_status(0) + "\n\n" + _BUNDLE_PROMPT,
                reply_markup=_bundle_edit_kb(product_id, category_id, page, count=0),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("edit_dttl_"):
            parts = data.split("_")
            product_id  = int(parts[2])
            category_id = int(parts[3]) if len(parts) > 3 else 0
            page        = int(parts[4]) if len(parts) > 4 else 0
            user_states[user_id] = UserState.EDITING_DIGITAL_TTL
            user_states[_uid(user_id, "product_id")] = product_id
            user_states[_uid(user_id, "category_id")] = category_id
            user_states[_uid(user_id, "page")] = page
            cancel_kb = InlineKeyboardBuilder()
            cancel_kb.row(InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"cancel_edit_digital_{product_id}_{category_id}_{page}"
            ))
            await call.message.edit_text(
                "⏰ Введите срок действия цифрового контента в часах (целое число ≥ 0).\n"
                "Отправьте 0 или «убрать» чтобы убрать срок.",
                reply_markup=cancel_kb.as_markup()
            )

        elif data.startswith("cancel_edit_digital_"):
            # Inline-«Отмена» из меню редактирования цифрового контента/TTL.
            parts = data.split("_")
            product_id  = int(parts[3])
            category_id = int(parts[4]) if len(parts) > 4 else 0
            page        = int(parts[5]) if len(parts) > 5 else 0
            user_states[user_id] = UserState.EDITING_PRODUCT
            for key in ("product_id", "category_id", "page", "digital_bundle"):
                user_states.pop(_uid(user_id, key), None)
            user_states[_uid(user_id, "product_id")] = product_id
            user_states[_uid(user_id, "category_id")] = category_id
            user_states[_uid(user_id, "page")] = page
            digital = await database.get_product_digital(product_id) or {}
            text = (
                "💾 <b>Цифровой контент</b>\n\n"
                f"Тип: {digital.get('kind') or '—'}\n"
                f"Срок (ч): {digital.get('ttl_hours') if digital.get('ttl_hours') else '—'}"
            )
            await call.message.edit_text(
                text,
                reply_markup=keyboards.create_digital_content_menu(product_id, category_id, page),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("clear_digital_"):
            parts = data.split("_")
            product_id = int(parts[2])
            await database.update_product_digital(product_id, None, None, None)
            await call.answer("✅ Цифровой контент очищен")

        # ── МОДЕРАЦИЯ ──
        elif data == "moderation_main":
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            await call.message.edit_text(
                "🛡️ <b>Панель модерации</b>\n\nВыберите раздел:",
                reply_markup=keyboards.create_moderation_main_menu(),
                parse_mode=ParseMode.HTML
            )

        elif data == "mod_under_review":
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            shops = await database.get_shops_under_review()
            if not shops:
                await call.message.edit_text(
                    "🔍 Магазинов на проверке нет.",
                    reply_markup=keyboards.create_back_button_menu("moderation_main")
                )
                return
            await call.message.edit_text(
                f"🔍 Магазины на проверке: {len(shops)}",
                reply_markup=keyboards.create_under_review_list_menu(shops)
            )

        elif data.startswith("mod_shop_clear_"):
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            shop_id = int(data.split("_")[-1])
            await database.set_shop_status(shop_id, database.SHOP_STATUS_ACTIVE, None)
            await database.resolve_complaints(shop_id, "dismissed")
            await call.answer("✅ Магазин снят с проверки")
            shops = await database.get_shops_under_review()
            if not shops:
                await call.message.edit_text(
                    "🔍 Магазинов на проверке нет.",
                    reply_markup=keyboards.create_back_button_menu("moderation_main")
                )
            else:
                await call.message.edit_text(
                    f"🔍 Магазины на проверке: {len(shops)}",
                    reply_markup=keyboards.create_under_review_list_menu(shops)
                )

        # ── Модерация: удаление магазина — шаг 1 (подтверждение) ──
        elif data.startswith("mod_shop_delete_") and not data.startswith("mod_shop_delete_do_"):
            if not config.is_owner(user_id):
                await call.answer("⛔ Удалять магазины может только владелец")
                return
            shop_id = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден")
                return
            confirm_kb = InlineKeyboardBuilder()
            confirm_kb.row(InlineKeyboardButton(
                text="🗑 Да, удалить безвозвратно",
                callback_data=f"mod_shop_delete_do_{shop_id}"
            ))
            confirm_kb.row(InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"mod_shop_{shop_id}"
            ))
            await call.message.edit_text(
                f"⚠️ <b>Удалить магазин «{shop_info[2]}»?</b>\n\n"
                "Действие <b>необратимо</b>. Будут удалены товары, категории, "
                "заказы, отзывы и токен бота. Владельцу магазина будет отправлено "
                "уведомление об удалении.",
                parse_mode=ParseMode.HTML,
                reply_markup=confirm_kb.as_markup()
            )

        # ── Модерация: удаление магазина — шаг 2 (фактическое удаление) ──
        elif data.startswith("mod_shop_delete_do_"):
            if not config.is_owner(user_id):
                await call.answer("⛔ Удалять магазины может только владелец")
                return
            shop_id = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            await database.delete_shop(shop_id)
            await _stop_shop_bot(shop_id)
            await call.answer("✅ Магазин удалён")
            if shop_info:
                try:
                    await bot.send_message(
                        shop_info[1],
                        f"⚠️ Ваш магазин «{shop_info[2]}» был удалён модерацией после проверки жалоб."
                    )
                except Exception:
                    pass
            shops = await database.get_shops_under_review()
            if not shops:
                await call.message.edit_text(
                    "🔍 Магазинов на проверке нет.",
                    reply_markup=keyboards.create_back_button_menu("moderation_main")
                )
            else:
                await call.message.edit_text(
                    f"🔍 Магазины на проверке: {len(shops)}",
                    reply_markup=keyboards.create_under_review_list_menu(shops)
                )

        elif data.startswith("mod_shop_") and not data.startswith("mod_shop_clear_") and not data.startswith("mod_shop_delete_"):
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            shop_id = int(data.split("_")[-1])
            shop_info = await database.get_shop_info(shop_id)
            if not shop_info:
                await call.answer("Магазин не найден")
                return
            complaints = await database.get_open_complaints(shop_id)
            text = f"🏪 <b>{shop_info[2]}</b>\nОткрытых жалоб: {len(complaints)}\n\n"
            for cid, cuser, reason, created, uname in complaints[:10]:
                who = f"@{uname}" if uname else f"id={cuser}"
                text += f"• #{cid} от {who} ({created}):\n  {reason}\n\n"
            await call.message.edit_text(
                text, reply_markup=keyboards.create_moderation_shop_menu(shop_id, config.is_owner(user_id)),
                parse_mode=ParseMode.HTML
            )

        elif data == "mod_open_disputes":
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            disputes = await database.get_open_disputes()
            if not disputes:
                await call.message.edit_text(
                    "⚖️ Открытых споров нет.",
                    reply_markup=keyboards.create_back_button_menu("moderation_main")
                )
                return
            await call.message.edit_text(
                f"⚖️ Открытых споров: {len(disputes)}",
                reply_markup=keyboards.create_open_disputes_menu(disputes)
            )

        elif data.startswith("mod_dispute_") and not data.startswith("mod_disp_resolve_"):
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            dispute_id = int(data.split("_")[-1])
            d = await database.get_dispute(dispute_id)
            if not d:
                await call.answer("Спор не найден")
                return
            order = await database.get_order(d["order_id"])
            messages = await database.get_dispute_messages(dispute_id)
            text = (
                f"⚖️ <b>Спор #{dispute_id}</b> по заказу #{d['order_id']}\n"
                f"Магазин: {d['shop_name']}\n"
                f"Открыл: {d['opener_role']} (id={d['opened_by']})\n"
                f"Причина: {d['reason']}\n\n"
            )
            if order:
                text += (
                    f"Товар: {order['product_name']}\n"
                    f"Кол-во: {order['quantity']}\n"
                    f"Сумма: {order['total_price']}₽\n"
                    f"Статус заказа: {database.ORDER_STATUS_LABELS.get(order['status'], order['status'])}\n\n"
                )
            if messages:
                text += "<b>Сообщения спора:</b>\n"
                for aid, role, body, created in messages[-10:]:
                    text += f"• [{role}] {body}\n"
            await call.message.edit_text(
                text, reply_markup=keyboards.create_dispute_resolution_menu(dispute_id),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("mod_disp_resolve_"):
            if not config.is_moderator(user_id):
                await call.answer("⛔ Нет прав")
                return
            parts = data.split("_")
            dispute_id = int(parts[3])
            resolution = parts[4]
            ok = await database.resolve_dispute(dispute_id, user_id, resolution, None)
            if not ok:
                await call.answer("Не удалось разрешить")
                return
            await call.answer("✅ Спор разрешён")
            await _notify_dispute_resolved(dispute_id, resolution)
            disputes = await database.get_open_disputes()
            if not disputes:
                await call.message.edit_text(
                    "⚖️ Открытых споров нет.",
                    reply_markup=keyboards.create_back_button_menu("moderation_main")
                )
            else:
                await call.message.edit_text(
                    f"⚖️ Открытых споров: {len(disputes)}",
                    reply_markup=keyboards.create_open_disputes_menu(disputes)
                )

        # ── Правила (клики) ──
        elif data == "terms_show":
            await call.message.edit_text(
                legal.TERMS_OF_USE,
                reply_markup=keyboards.create_terms_back_menu("terms_back"),
                parse_mode=ParseMode.HTML
            )

        elif data == "terms_legal":
            await call.message.edit_text(
                legal.PROHIBITED_GOODS,
                reply_markup=keyboards.create_terms_back_menu("terms_back"),
                parse_mode=ParseMode.HTML
            )

        elif data == "terms_back":
            await call.message.edit_text(
                f"👋 Для продолжения примите Правила платформы.\n\n{legal.DISCLAIMER_SHORT}",
                reply_markup=keyboards.create_terms_acceptance_menu(),
                parse_mode=ParseMode.HTML
            )

        elif data == "terms_accept":
            await database.accept_terms(user_id, legal.TERMS_VERSION)
            user_states[user_id] = UserState.MAIN_MENU
            # Если перед принятием правил у пользователя был pay-deeplink
            # (он пришёл по ссылке /start pay_… из магазин-бота), сразу
            # выставляем счёт, а не показываем большое приветствие — иначе
            # ему придётся снова идти в магазин и кликать ту же ссылку.
            pending_arg = user_states.pop(_uid(user_id, "pending_pay_arg"), None)
            if pending_arg:
                try:
                    await call.message.edit_text(
                        "✅ Спасибо! Правила приняты. Сейчас откроется окно оплаты."
                    )  # noqa: E501
                except Exception:
                    pass
                await _handle_pay_deeplink(call.message.chat.id, user_id, pending_arg)
            else:
                await call.message.edit_text(
                    "✅ Спасибо! Правила приняты.\n\n" + WELCOME_TEXT,
                    reply_markup=keyboards.create_main_menu()
                )

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


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.ADDING_WORKER), ~F.successful_payment)
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


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.WITHDRAW_AMOUNT), ~F.successful_payment)
async def handle_withdraw_amount(message: Message):
    user_id = message.from_user.id
    shop_id = user_states.get(_uid(user_id, "shop_id"))
    method  = user_states.get(_uid(user_id, "withdraw_method"))
    raw     = (message.text or "").strip().replace(",", ".")
    if raw.lower() == "назад":
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("❌ Запрос вывода отменён",
                             reply_markup=keyboards.create_finance_menu(shop_id) if shop_id else keyboards.create_main_menu())
        return
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("❌ Введите число — сумму в рублях.")
        return
    if amount < config.MIN_WITHDRAWAL:
        await message.answer(f"❌ Минимальная сумма вывода — {config.MIN_WITHDRAWAL} ₽.")
        return
    bal = await database.get_seller_balance(shop_id) if shop_id else None
    if not bal or amount > bal["amount_rub"] + 0.001:
        avail = bal["amount_rub"] if bal else 0.0
        await message.answer(f"❌ На балансе только {avail:.2f} ₽. Введите меньшую сумму.")
        return
    user_states[_uid(user_id, "withdraw_amount")] = amount
    user_states[user_id] = UserState.WITHDRAW_REQUISITES
    placeholders = {
        "card":     "номер карты, 16 цифр",
        "sbp":      "номер телефона и название банка (например, +79991234567 Тинькофф)",
        "business": "ИНН, расчётный счёт, БИК",
        "crypto":   "сеть и адрес (например, USDT TRC20: TXxx...)",
    }
    method_label = database.WITHDRAWAL_METHOD_LABELS.get(method, method)
    await message.answer(
        f"Сумма: <b>{amount:.2f} ₽</b>\nСпособ: {method_label}\n\n"
        f"Отправьте реквизиты одним сообщением.\nФормат: {placeholders.get(method, '')}\n\n"
        f"Отправьте 'назад' для отмены.",
        parse_mode=ParseMode.HTML
    )


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.WITHDRAW_REQUISITES), ~F.successful_payment)
async def handle_withdraw_requisites(message: Message):
    user_id = message.from_user.id
    shop_id = user_states.get(_uid(user_id, "shop_id"))
    method  = user_states.get(_uid(user_id, "withdraw_method"))
    amount  = user_states.get(_uid(user_id, "withdraw_amount"))
    raw     = (message.text or "").strip()
    if raw.lower() == "назад":
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("❌ Запрос вывода отменён",
                             reply_markup=keyboards.create_finance_menu(shop_id) if shop_id else keyboards.create_main_menu())
        return
    if not shop_id or not method or not amount:
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("❌ Сессия запроса потеряна. Начните заново.",
                             reply_markup=keyboards.create_main_menu())
        return
    if len(raw) < 4:
        await message.answer("❌ Реквизиты слишком короткие. Введите ещё раз.")
        return
    wid = await database.create_withdrawal(shop_id, user_id, amount, method, raw)
    user_states.pop(_uid(user_id, "withdraw_amount"), None)
    user_states.pop(_uid(user_id, "withdraw_method"), None)
    user_states[user_id] = UserState.SHOP_MENU
    if not wid:
        await message.answer(
            "❌ Не удалось создать запрос (недостаточно средств?). Проверьте баланс.",
            reply_markup=keyboards.create_finance_menu(shop_id)
        )
        return
    shop_info = await database.get_shop_info(shop_id)
    shop_name = shop_info[2] if shop_info else f"shop#{shop_id}"
    await message.answer(
        f"✅ Запрос вывода #{wid} создан и автоматически одобрен.\n\n"
        f"Сумма: {amount:.2f} ₽\n"
        f"Способ: {database.WITHDRAWAL_METHOD_LABELS.get(method, method)}\n\n"
        f"Владелец платформы получит уведомление и переведёт деньги по реквизитам.",
        reply_markup=keyboards.create_finance_menu(shop_id)
    )
    # Уведомляем всех владельцев платформы
    method_label = database.WITHDRAWAL_METHOD_LABELS.get(method, method)
    masked_req = config.mask_secret(raw, visible=4)
    notify_text = (
        f"💸 <b>Новый запрос на вывод #{wid}</b>\n\n"
        f"Магазин: {shop_name} (id={shop_id})\n"
        f"Продавец: <code>{user_id}</code>\n"
        f"Сумма: <b>{amount:.2f} ₽</b>\n"
        f"Способ: {method_label}\n"
        f"Реквизиты (маска): <code>{masked_req}</code>\n\n"
        f"Полные реквизиты:\n<code>{raw}</code>"
    )
    for owner_id in config.OWNER_IDS:
        try:
            await bot.send_message(
                owner_id, notify_text, parse_mode=ParseMode.HTML,
                reply_markup=keyboards.create_withdraw_owner_menu(wid)
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить OWNER {owner_id} о выводе #{wid}: {e}")


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.WITHDRAW_REJECT_NOTE), ~F.successful_payment)
async def handle_withdraw_reject_note(message: Message):
    user_id = message.from_user.id
    if not config.is_owner(user_id):
        user_states[user_id] = UserState.MAIN_MENU
        return
    wid = user_states.get(_uid(user_id, "withdrawal_id"))
    note = (message.text or "").strip()[:500]
    user_states[user_id] = UserState.MAIN_MENU
    user_states.pop(_uid(user_id, "withdrawal_id"), None)
    if not wid:
        await message.answer("❌ Сессия отклонения потеряна.")
        return
    ok = await database.reject_withdrawal(wid, owner_note=note or None)
    if not ok:
        await message.answer("❌ Не удалось отклонить (вывод уже обработан?).")
        return
    w = await database.get_withdrawal(wid)
    await message.answer(f"❌ Вывод #{wid} отклонён, средства возвращены продавцу на баланс.")
    if w:
        try:
            await bot.send_message(
                w["seller_user_id"],
                f"❌ Ваш запрос на вывод #{w['id']} отклонён.\n"
                f"Сумма {w['amount_rub']:.2f} ₽ возвращена на баланс.\n"
                + (f"Причина: {note}" if note else "")
            )
        except Exception:
            pass


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.ADDING_PROMO_CODE), ~F.successful_payment)
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


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.ADDING_PROMO_VALUE), ~F.successful_payment)
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

# ВАЖНО: catch-all регистрируется ВРУЧНУЮ в самом низу файла, ПОСЛЕ всех
# state-обработчиков (см. dp.message.register(text_handler, ...) ниже). Если
# повесить @dp.message здесь, в порядке регистрации aiogram catch-all окажется
# раньше handle_edit_digital_content, handle_edit_digital_ttl,
# handle_withdraw_amount и т.п. — и съест их сообщения, отправляя
# пользователя в главное меню («Используйте кнопки меню для навигации»).
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

        # Если контекст редактирования потерялся (бот перезапустился, состояние
        # испортилось и т.п.), не строим меню с callback_data вроде
        # `digital_menu_None_None_0` — оно потом падает при клике.
        if product_id is None or category_id is None:
            user_states[user_id] = UserState.MAIN_MENU
            _clear_product_state(user_id)
            await message.answer(
                "⚠️ Сессия редактирования товара истекла. Откройте товар из списка заново.",
                reply_markup=keyboards.create_main_menu()
            )
            return

        if message.text and message.text.strip().lower() == 'назад':
            await message.answer("❌ Изменение товара отменено")
            # Возвращаемся к меню РЕДАКТИРОВАНИЯ ТОВАРА (уровнем выше),
            # а не в общий список товаров — так удобнее продолжать правки.
            await message.answer(
                "Выберите действие:",
                reply_markup=keyboards.create_edit_product_menu(product_id, category_id, page)
            )
            user_states[user_id] = UserState.EDITING_PRODUCT
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

        # После любого изменения свойства товара возвращаем меню редактирования
        # самого товара, чтобы можно было сразу поправить ещё одно поле без
        # лишней навигации (раньше выкидывало в список товаров и SHOP_MENU).
        await message.answer(
            "Выберите действие:",
            reply_markup=keyboards.create_edit_product_menu(product_id, category_id, page)
        )
        user_states[user_id] = UserState.EDITING_PRODUCT
        # Очищаем только тип правки — id товара/категории/страница нужны
        # для корректной работы меню при повторных кликах.
        user_states.pop(_uid(user_id, "edit_type"), None)
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
    # Возврат к меню редактирования товара (уровень выше), а не к списку.
    await message.answer(
        "Выберите действие:",
        reply_markup=keyboards.create_edit_product_menu(product_id, category_id, page)
    )
    user_states[user_id] = UserState.EDITING_PRODUCT
    user_states.pop(_uid(user_id, "edit_type"), None)


# ──────────────────────────────────────────────────────────────────────────────
#  Хелперы: форматирование/уведомления заказов и споров
# ──────────────────────────────────────────────────────────────────────────────

def _format_order_for_admin(order: dict) -> str:
    label = database.ORDER_STATUS_LABELS.get(order["status"], order["status"])
    digital_mark = " 💾 (цифровой)" if order["is_digital"] else " 📦 (физический)"
    customer = ("@" + order["username"]) if order.get("username") else f"id={order['customer_user_id']}"
    text = (
        f"📋 <b>Заказ #{order['id']}</b>{digital_mark}\n"
        f"Статус: {label}\n"
        f"Покупатель: {customer}\n"
        f"Товар: <b>{order['product_name']}</b>\n"
        f"Кол-во: {order['quantity']}\n"
        f"Сумма: {order['total_price']}₽\n"
        f"Способ оплаты: {database.payment_method_label(order['payment_method'])}\n"
        f"Адрес/контакт: {order['delivery_address']}\n"
        f"Создан: {order['created_at']}\n"
    )
    if order.get("paid_at"):
        text += f"Оплачен: {order['paid_at']}\n"
    if order.get("delivered_at"):
        text += f"Доставлен: {order['delivered_at']}\n"
    if order.get("closed_at"):
        text += f"Закрыт: {order['closed_at']}\n"
    if order.get("delivery_payload"):
        text += f"\n<b>Цифровой контент отправлен:</b>\n<code>{order['delivery_payload'][:300]}</code>\n"
    if order.get("seller_note"):
        text += f"\n<b>Заметка продавца:</b> {order['seller_note']}\n"
    return text


async def _send_via_shop_or_main(shop_id: int, customer_id: int, text: str,
                                 photo_id: Optional[str] = None,
                                 file_id: Optional[str] = None,
                                 photo_path: Optional[str] = None,
                                 file_path: Optional[str] = None) -> bool:
    """Пытается отправить от имени магазин-бота, иначе — от менеджера.

    `photo_path`/`file_path` — локальные файлы (FSInputFile). Используются для
    цифрового контента, который нельзя пересылать между разными ботами по
    Telegram file_id.
    """
    sender = active_shop_bots.get(shop_id) or bot
    try:
        if photo_path:
            await sender.send_photo(customer_id, FSInputFile(photo_path),
                                    caption=text or None, parse_mode=ParseMode.HTML)
        elif file_path:
            await sender.send_document(customer_id, FSInputFile(file_path),
                                       caption=text or None, parse_mode=ParseMode.HTML)
        elif photo_id:
            await sender.send_photo(customer_id, photo_id, caption=text or None,
                                    parse_mode=ParseMode.HTML)
        elif file_id:
            await sender.send_document(customer_id, file_id, caption=text or None,
                                       parse_mode=ParseMode.HTML)
        else:
            await sender.send_message(customer_id, text, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        logger.error(f"Не удалось отправить покупателю {customer_id} (магазин {shop_id}): {e}")
        return False


async def _notify_customer_status_change(order_id: int, new_status: str) -> None:
    order = await database.get_order(order_id)
    if not order:
        return
    label = database.ORDER_STATUS_LABELS.get(new_status, new_status)
    msg = (
        f"🔔 Статус заказа #{order_id} обновлён: <b>{label}</b>\n"
        f"Магазин: {order['shop_name']}\n"
        f"Товар: {order['product_name']} ×{order['quantity']}"
    )
    await _send_via_shop_or_main(order["shop_id"], order["customer_user_id"], msg)


async def _deliver_digital_content(order: dict) -> bool:
    """Отправляет цифровой контент покупателю и помечает заказ как `delivered`.

    Поддерживает одиночные элементы и пакеты (kind='bundle'). Пытается
    отправить от имени магазин-бота; при сбое — от менеджер-бота (file_id
    между ботами не работает, поэтому медиа хранятся как файлы на диске).
    """
    digital_kind = order.get("digital_content_kind")
    digital      = order.get("digital_content")
    ttl_hours    = order.get("digital_ttl_hours")
    if not digital_kind or not digital:
        return False
    header = f"📤 <b>Ваш товар:</b> {order['product_name']}"
    if ttl_hours:
        header += f"\n⏰ Срок действия: {ttl_hours} ч с момента оплаты."
    payload_for_log = f"[{digital_kind}] {digital[:200]}"
    sender = active_shop_bots.get(order["shop_id"]) or bot
    sent = await digital_delivery.deliver_digital(
        sender, order["customer_user_id"], digital_kind, digital, header=header
    )
    if not sent and sender is not bot:
        # Шоп-бот не сработал — пробуем платформу.
        sent = await digital_delivery.deliver_digital(
            bot, order["customer_user_id"], digital_kind, digital, header=header
        )
    if sent:
        await database.set_order_delivery_payload(order["id"], payload_for_log)
        await database.update_order_status(order["id"], database.ORDER_STATUS_DELIVERED)
    return sent


async def _notify_dispute_resolved(dispute_id: int, resolution: str) -> None:
    d = await database.get_dispute(dispute_id)
    if not d:
        return
    order = await database.get_order(d["order_id"])
    if not order:
        return
    res_label = {
        "refund":   "💸 решено в пользу покупателя — возврат средств",
        "complete": "✅ решено в пользу продавца — заказ завершён",
        "reject":   "⛔ спор отклонён модерацией",
    }.get(resolution, resolution)
    customer_msg = (
        f"⚖️ Спор #{dispute_id} по заказу #{order['id']} закрыт:\n{res_label}\n\n"
        f"Если решение — возврат, продавец оформит его в своём кабинете "
        f"PayMaster/ЮKassa в течение 3 рабочих дней."
    )
    await _send_via_shop_or_main(d["shop_id"], order["customer_user_id"], customer_msg)
    shop_info = await database.get_shop_info(d["shop_id"])
    if not shop_info:
        return
    seller_id = shop_info[1]
    try:
        await bot.send_message(
            seller_id,
            f"⚖️ Спор #{dispute_id} по заказу #{order['id']} закрыт:\n{res_label}",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
#  Сообщения: цифровой контент, TTL, ответы по заказу/спору
# ──────────────────────────────────────────────────────────────────────────────

# ── Сборка пакета цифрового контента ───────────────────────────────────────
# Продавец может прислать произвольное число сообщений (фото / видео / голос
# / документ / текст / ссылка); все они накапливаются в `digital_bundle`,
# затем сохраняются как одиночный элемент (если он один) или как пакет
# kind='bundle' с JSON-списком элементов.

_BUNDLE_PROMPT = (
    "📝 Отправьте материалы для покупателя. Можно несколько подряд:\n\n"
    "• текст или ссылка\n"
    "• фото, видео, голос, кружок, GIF\n"
    "• документ любого формата (zip, pdf, mp3, apk и т.д.)\n\n"
    "Когда добавите всё — нажмите «✅ Сохранить»."
)


def _format_bundle_status(count: int) -> str:
    if count == 0:
        return "💾 Пакет цифрового контента: <b>пуст</b>"
    return f"💾 Пакет цифрового контента: <b>{count} эл.</b>"


def _bundle_edit_kb(product_id: int, category_id: int, page: int, count: int):
    builder = InlineKeyboardBuilder()
    if count > 0:
        builder.row(InlineKeyboardButton(
            text=f"✅ Сохранить ({count})",
            callback_data=f"dbundle_save_{product_id}_{category_id}_{page}"
        ))
        builder.row(InlineKeyboardButton(
            text="🗑 Очистить пакет",
            callback_data=f"dbundle_reset_{product_id}_{category_id}_{page}"
        ))
    builder.row(InlineKeyboardButton(
        text="❌ Отмена",
        callback_data=f"cancel_edit_digital_{product_id}_{category_id}_{page}"
    ))
    return builder.as_markup()


async def _save_message_to_disk(file_id: str, default_ext: str = "",
                                preferred_name: str = "") -> str:
    """Скачивает файл с серверов Telegram и кладёт в `digital_content/`.

    Возвращает относительный путь. Имя — uuid-префикс + (preferred_name
    либо расширение из исходного `file_path`).
    """
    os.makedirs("digital_content", exist_ok=True)
    file_info = await bot.get_file(file_id)
    file_data = await bot.download_file(file_info.file_path)
    if preferred_name:
        path = f"digital_content/{uuid.uuid4().hex}_{preferred_name}"
    else:
        ext = os.path.splitext(file_info.file_path)[1] or default_ext
        path = f"digital_content/{uuid.uuid4().hex}{ext}"
    with open(path, "wb") as f:
        f.write(file_data.read())
    return path


async def _extract_digital_item(message: Message) -> Optional[dict]:
    """Возвращает {"kind": ..., "content": ..., "caption": Optional[str]} или None.

    Поддерживает текст, ссылки, фото, видео, аудио, голос, видеосообщение,
    анимацию (GIF), документы любого формата.
    """
    caption = (message.caption or "").strip() or None
    if message.photo:
        path = await _save_message_to_disk(message.photo[-1].file_id, default_ext=".jpg")
        return {"kind": "photo_path", "content": path, "caption": caption}
    if message.video:
        path = await _save_message_to_disk(message.video.file_id, default_ext=".mp4")
        return {"kind": "video_path", "content": path, "caption": caption}
    if message.animation:
        path = await _save_message_to_disk(message.animation.file_id, default_ext=".mp4")
        return {"kind": "animation_path", "content": path, "caption": caption}
    if message.audio:
        name = message.audio.file_name or ""
        path = await _save_message_to_disk(message.audio.file_id, default_ext=".mp3",
                                           preferred_name=name)
        return {"kind": "audio_path", "content": path, "caption": caption}
    if message.voice:
        path = await _save_message_to_disk(message.voice.file_id, default_ext=".ogg")
        return {"kind": "voice_path", "content": path, "caption": caption}
    if message.video_note:
        path = await _save_message_to_disk(message.video_note.file_id, default_ext=".mp4")
        return {"kind": "video_note_path", "content": path, "caption": caption}
    if message.document:
        original_name = message.document.file_name or ""
        path = await _save_message_to_disk(message.document.file_id, default_ext="",
                                           preferred_name=original_name)
        return {"kind": "file_path", "content": path, "caption": caption}
    text = (message.text or "").strip()
    if text:
        if text.lower().startswith(("http://", "https://", "tg://")):
            return {"kind": "url", "content": text, "caption": None}
        return {"kind": "text", "content": text, "caption": None}
    return None


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.EDITING_DIGITAL_CONTENT), ~F.successful_payment)
async def handle_edit_digital_content(message: Message):
    user_id    = message.from_user.id
    product_id = user_states.get(_uid(user_id, "product_id"))
    if not product_id:
        user_states[user_id] = UserState.SHOP_MENU
        return
    text = (message.text or "").strip()
    if text.lower() == "назад":
        user_states[user_id] = UserState.SHOP_MENU
        user_states[_uid(user_id, "digital_bundle")] = []
        await message.answer("❌ Отменено")
        return

    item = await _extract_digital_item(message)
    if not item:
        await message.answer(
            "❌ Не понял, что отправлено. Можно: текст, ссылку, фото, видео, "
            "аудио, голос, кружок, GIF или документ."
        )
        return

    bundle_key = _uid(user_id, "digital_bundle")
    items = user_states.get(bundle_key)
    if not isinstance(items, list):
        items = []
    items.append(item)
    user_states[bundle_key] = items

    category_id = user_states.get(_uid(user_id, "category_id"), 0) or 0
    page        = user_states.get(_uid(user_id, "page"), 0) or 0
    kind_lbl = {
        "text": "текст", "url": "ссылка",
        "photo_path": "фото", "video_path": "видео",
        "audio_path": "аудио", "voice_path": "голосовое",
        "video_note_path": "кружок", "animation_path": "GIF",
        "file_path": "файл",
    }.get(item["kind"], item["kind"])

    await message.answer(
        f"➕ Добавлено: <b>{kind_lbl}</b>\n\n"
        + _format_bundle_status(len(items)) + "\n\n" + _BUNDLE_PROMPT,
        reply_markup=_bundle_edit_kb(product_id, category_id, page, count=len(items)),
        parse_mode=ParseMode.HTML
    )


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.EDITING_DIGITAL_TTL), ~F.successful_payment)
async def handle_edit_digital_ttl(message: Message):
    user_id    = message.from_user.id
    product_id = user_states.get(_uid(user_id, "product_id"))
    if not product_id:
        user_states[user_id] = UserState.SHOP_MENU
        return
    text = (message.text or "").strip().lower()
    if text in ("назад", "убрать", ""):
        await database.update_product_digital(
            product_id,
            (await database.get_product_digital(product_id) or {}).get("kind"),
            (await database.get_product_digital(product_id) or {}).get("content"),
            None
        )
        user_states[user_id] = UserState.SHOP_MENU
        await message.answer("✅ Срок действия убран")
        return
    try:
        ttl = int(text)
        if ttl < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число ≥ 0")
        return
    info = await database.get_product_digital(product_id) or {}
    await database.update_product_digital(product_id, info.get("kind"), info.get("content"),
                                          ttl if ttl > 0 else None)
    category_id = user_states.get(_uid(user_id, "category_id"), 0) or 0
    page        = user_states.get(_uid(user_id, "page"), 0) or 0
    user_states[user_id] = UserState.EDITING_PRODUCT
    digital = await database.get_product_digital(product_id) or {}
    msg = (
        ("✅ Срок действия сохранён.\n\n" if ttl > 0 else "✅ Срок действия убран.\n\n") +
        "💾 <b>Цифровой контент</b>\n"
        f"Тип: {digital.get('kind') or '—'}\n"
        f"Срок (ч): {digital.get('ttl_hours') if digital.get('ttl_hours') else '—'}"
    )
    await message.answer(
        msg,
        reply_markup=keyboards.create_digital_content_menu(product_id, category_id, page),
        parse_mode=ParseMode.HTML
    )


@dp.message(F.func(lambda m: user_states.get(m.from_user.id) == UserState.REPLYING_DISPUTE), ~F.successful_payment)
async def handle_admin_reply(message: Message):
    user_id   = message.from_user.id
    order_id  = user_states.get(_uid(user_id, "reply_order_id"))
    kind      = user_states.get(_uid(user_id, "reply_kind"), "order")
    text      = (message.text or "").strip()

    if text.lower() == "назад":
        user_states[user_id] = UserState.SHOP_MENU
        user_states.pop(_uid(user_id, "reply_order_id"), None)
        user_states.pop(_uid(user_id, "reply_kind"), None)
        await message.answer("❌ Отменено")
        return
    if not order_id or not text:
        await message.answer("❌ Пустое сообщение")
        return

    order = await database.get_order(order_id)
    if not order:
        await message.answer("❌ Заказ не найден")
        user_states[user_id] = UserState.SHOP_MENU
        return

    if kind == "dispute_seller":
        did = await database.open_dispute(order_id, user_id, "seller", text)
        if not did:
            await message.answer("❌ Не удалось открыть спор")
            user_states[user_id] = UserState.SHOP_MENU
            return
        await database.add_dispute_message(did, user_id, "seller", text)
        await _send_via_shop_or_main(
            order["shop_id"], order["customer_user_id"],
            f"⚖️ Продавец открыл спор по заказу #{order_id}:\n{text}\n\n"
            f"Откройте «Мои заказы» в магазине для ответа."
        )
        await message.answer(f"✅ Спор #{did} открыт")
    else:
        # Обычное сообщение продавец → покупатель
        await _send_via_shop_or_main(
            order["shop_id"], order["customer_user_id"],
            f"💬 Сообщение от продавца по заказу #{order_id}:\n{text}"
        )
        await message.answer("✅ Сообщение отправлено покупателю")

    user_states[user_id] = UserState.SHOP_MENU
    user_states.pop(_uid(user_id, "reply_order_id"), None)
    user_states.pop(_uid(user_id, "reply_kind"), None)


# ──────────────────────────────────────────────────────────────────────────────
#  Платежи: pre_checkout + successful_payment
# ──────────────────────────────────────────────────────────────────────────────

@dp.pre_checkout_query()
async def manager_pre_checkout(query: PreCheckoutQuery):
    payload = query.invoice_payload or ""
    # ── Группа заказов (корзина) ──
    if payload.startswith("group_"):
        group_id = payload[len("group_"):]
        if not group_id:
            await query.answer(ok=False, error_message="Некорректная корзина.")
            return
        orders = await database.get_orders_by_group(group_id)
        if not orders:
            await query.answer(ok=False, error_message="Корзина не найдена.")
            return
        pending = [o for o in orders
                   if o["customer_user_id"] == query.from_user.id
                   and o["status"] == database.ORDER_STATUS_NEW]
        if not pending:
            await query.answer(ok=False, error_message="Корзина уже оплачена или отменена.")
            return
        if any(o["customer_user_id"] != query.from_user.id for o in orders):
            await query.answer(ok=False, error_message="Корзина принадлежит другому пользователю.")
            return
        expected = sum(int(round(float(o["total_price"] or 0) * 100)) for o in pending)
        if query.total_amount != expected:
            await query.answer(ok=False, error_message="Сумма не совпадает с корзиной.")
            return
        await query.answer(ok=True)
        return
    # ── Одиночный заказ ──
    order_id = 0
    if payload.startswith("order_"):
        try:
            order_id = int(payload.split("_", 1)[1])
        except (ValueError, IndexError):
            order_id = 0
    if not order_id:
        await query.answer(ok=False, error_message="Некорректный заказ.")
        return
    order = await database.get_order(order_id)
    if not order:
        await query.answer(ok=False, error_message="Заказ не найден.")
        return
    if order["status"] != database.ORDER_STATUS_NEW:
        await query.answer(ok=False, error_message="Заказ уже оплачен или отменён.")
        return
    if order["customer_user_id"] != query.from_user.id:
        await query.answer(ok=False, error_message="Заказ принадлежит другому пользователю.")
        return
    expected = int(round(float(order["total_price"] or 0) * 100))
    if query.total_amount != expected:
        await query.answer(ok=False, error_message="Сумма не совпадает с заказом.")
        return
    await query.answer(ok=True)


async def _process_successful_group_payment(message: Message, group_id: str) -> None:
    """Обрабатывает успешную оплату корзины (group_id): переводит все заказы
    группы в PAID, начисляет баланс продавцу, доставляет цифру и шлёт ОДНО
    итоговое уведомление покупателю и админам магазина."""
    if not group_id:
        await message.answer("✅ Платёж получен, но корзина не определена.")
        return
    orders = await database.get_orders_by_group(group_id)
    pending = [o for o in orders
               if o["customer_user_id"] == message.from_user.id
               and o["status"] == database.ORDER_STATUS_NEW]
    if not pending:
        await message.answer("✅ Платёж получен, но эта корзина уже была обработана.")
        return
    paid_ids: list[int] = []
    digital_orders: list[dict] = []
    total = 0.0
    shop_id = pending[0]["shop_id"]
    shop_name = pending[0].get("shop_name") or f"shop#{shop_id}"
    for o in pending:
        ok = await database.update_order_status(o["id"], database.ORDER_STATUS_PAID)
        if ok:
            paid_ids.append(o["id"])
            total += float(o["total_price"] or 0)
            fresh = await database.get_order(o["id"])
            if fresh and fresh.get("digital_content"):
                digital_orders.append(fresh)
    # 1. Покупателю в МЕНЕДЖЕР-боте — единое подтверждение.
    try:
        ids_str = ", ".join(f"#{i}" for i in paid_ids) or "—"
        await message.answer(
            f"✅ Корзина оплачена!\n"
            f"Магазин: {shop_name}\n"
            f"Заказы: {ids_str}\n"
            f"Сумма: {total:.2f} ₽\n\n"
            f"Возвращайтесь в магазин-бот — статусы заказов обновлены."
        )
    except Exception as e:
        logger.error(f"manager notify buyer (group) failed for {group_id}: {e}")
    # 2. Покупателю в МАГАЗИН-боте — дублируем, если бот поднят.
    shop_sender = active_shop_bots.get(shop_id)
    if shop_sender:
        try:
            await shop_sender.send_message(
                pending[0]["customer_user_id"],
                f"💳 Корзина оплачена!\n"
                f"Заказы: {', '.join(f'#{i}' for i in paid_ids)}\n"
                f"Сумма: {total:.2f} ₽\n"
                f"Подробности — в разделе «Мои заказы»."
            )
        except Exception as e:
            logger.error(f"shop-bot notify buyer (group) failed for {group_id}: {e}")
    # 3. Цифровая доставка по каждой строке.
    for o in digital_orders:
        try:
            await _deliver_digital_content(o)
        except Exception as e:
            logger.error(f"digital delivery failed for order {o['id']}: {e}")
    # 4. Продавцу/админам — одно итоговое уведомление по корзине.
    admin_ids: list[int] = []
    shop_info = await database.get_shop_info(shop_id)
    if shop_info:
        admin_ids = [shop_info[1]] + await database.get_shop_admins_ids(shop_id)
    notify_text = (
        f"💳 Поступила оплата корзины\n"
        f"Магазин: {shop_name}\n"
        f"Заказы: {', '.join(f'#{i}' for i in paid_ids)} ({len(paid_ids)} поз.)\n"
        f"Сумма: <b>{total:.2f} ₽</b> → начислено на ваш баланс платформы."
    )
    for aid in set(admin_ids):
        try:
            await bot.send_message(aid, notify_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"manager notify admin {aid} failed for group {group_id}: {e}")
        if shop_sender:
            try:
                await shop_sender.send_message(aid, notify_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"shop-bot notify admin {aid} failed for group {group_id}: {e}")


@dp.message(F.successful_payment)
async def manager_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload or ""
    # ── Группа заказов (корзина) ──
    if payload.startswith("group_"):
        await _process_successful_group_payment(message, payload[len("group_"):])
        return
    if not payload.startswith("order_"):
        await message.answer("✅ Платёж получен.")
        return
    try:
        order_id = int(payload.split("_", 1)[1])
    except (ValueError, IndexError):
        await message.answer("✅ Платёж получен, но не удалось определить заказ.")
        return
    order = await database.get_order(order_id)
    if not order:
        await message.answer(f"✅ Платёж получен, но заказ #{order_id} не найден.")
        return
    # Переводим в paid через update_order_status — это автоматически зачислит
    # деньги на внутренний баланс продавца (для платёжного метода != cash).
    await database.update_order_status(order_id, database.ORDER_STATUS_PAID)
    order = await database.get_order(order_id)  # перечитаем с обновлённым статусом
    shop_id = order["shop_id"]
    shop_name = order.get("shop_name") or f"shop#{shop_id}"
    total = float(order["total_price"] or 0)
    # 1. Покупателю: подтверждение в МЕНЕДЖЕР-боте (там, где он платил).
    try:
        await message.answer(
            f"✅ Заказ #{order_id} оплачен!\n"
            f"Магазин: {shop_name}\n"
            f"Сумма: {total:.2f} ₽\n\n"
            f"Возвращайтесь в магазин-бот — там увидите статус «оплачен» в «Мои заказы»."
        )
    except Exception as e:
        logger.error(f"manager notify buyer failed for order {order_id}: {e}")
    # 2. Покупателю: дублирующее уведомление в МАГАЗИН-боте, чтобы он
    #    увидел подтверждение в той же ленте, где совершал покупку.
    shop_sender = active_shop_bots.get(shop_id)
    if shop_sender:
        try:
            await shop_sender.send_message(
                order["customer_user_id"],
                f"💳 Ваш заказ #{order_id} оплачен!\n"
                f"Сумма: {total:.2f} ₽\n"
                f"Подробности — в разделе «Мои заказы»."
            )
        except Exception as e:
            logger.error(f"shop-bot notify buyer failed for order {order_id}: {e}")
    # 3. Цифровая доставка идёт через магазин-бот (если поднят), потому что
    #    file_id Telegram между ботами не работает; для путей на диске мы
    #    используем FSInputFile и можем отправить откуда угодно.
    #    _deliver_digital_content также переводит заказ в DELIVERED — это
    #    корректно для цифры (товар у покупателя сразу).
    if order.get("digital_content"):
        try:
            await _deliver_digital_content(order)
        except Exception as e:
            logger.error(f"digital delivery failed for order {order_id}: {e}")
    # 4. Продавцу/админам магазина — уведомление в менеджер-боте + в shop-bot.
    admin_ids: list[int] = []
    shop_info = await database.get_shop_info(shop_id)
    if shop_info:
        admin_ids = [shop_info[1]] + await database.get_shop_admins_ids(shop_id)
    notify_text = (
        f"💳 Поступила оплата по заказу #{order_id}\n"
        f"Магазин: {shop_name}\n"
        f"Товар: {order.get('product_name')} ×{order['quantity']}\n"
        f"Сумма: <b>{total:.2f} ₽</b> → начислено на ваш баланс платформы."
    )
    for aid in set(admin_ids):
        try:
            await bot.send_message(aid, notify_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"manager notify admin {aid} failed for order #{order_id}: {e}")
        if shop_sender:
            try:
                await shop_sender.send_message(aid, notify_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"shop-bot notify admin {aid} failed for order #{order_id}: {e}")


# Регистрируем catch-all text_handler САМЫМ ПОСЛЕДНИМ среди message-хендлеров,
# чтобы все state-обработчики выше (handle_edit_digital_content,
# handle_edit_digital_ttl, handle_reply_dispute, handle_withdraw_*, ADD/EDIT
# промокодов и т.п.) имели приоритет. ~F.successful_payment гарантирует, что
# сервисное сообщение об оплате уйдёт в manager_successful_payment.
dp.message.register(text_handler, ~F.successful_payment)


# ──────────────────────────────────────────────────────────────────────────────
#  Точка входа
# ──────────────────────────────────────────────────────────────────────────────

async def main():
    print("Инициализация базы данных...")
    database.init_database()
    print("База данных готова!")

    # Узнаём username менеджер-бота — он нужен магазин-ботам для deeplink-оплаты.
    if not config.MANAGER_BOT_USERNAME:
        try:
            me = await bot.get_me()
            if me and me.username:
                config.MANAGER_BOT_USERNAME = me.username
                print(f"MANAGER_BOT_USERNAME автоопределён: @{me.username}")
        except Exception as e:
            logger.error(f"Не удалось получить username менеджер-бота: {e}")

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