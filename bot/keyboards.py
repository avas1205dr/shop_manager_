"""
keyboards.py  —  async-совместимые клавиатуры для aiogram 3.x
Все функции, требующие обращения к БД, переписаны как async.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import database


# ─────────────────── ГЛАВНОЕ МЕНЮ МЕНЕДЖЕРА ───────────────────

def create_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Рейтинг", callback_data="reviews"),
        InlineKeyboardButton(text="🏪 Мои магазины", callback_data="my_shops"),
    )
    return builder.as_markup()


# ─────────────────── РЕЙТИНГ ───────────────────

async def create_reviews_menu(page: int = 0, per_page: int = 5) -> InlineKeyboardMarkup:
    shops = await database.get_shops_with_ratings()
    builder = InlineKeyboardBuilder()

    start = page * per_page
    end = min(start + per_page, len(shops))

    for shop_id, shop_name, bot_username, avg_rating, review_count in shops[start:end]:
        avg_rating = float(avg_rating or 0)
        stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
        builder.row(InlineKeyboardButton(
            text=f"{shop_name}\n{stars} ({review_count} отзывов)",
            callback_data=f"shop_detail_{shop_id}"
        ))

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"reviews_page_{page - 1}"))
    if end < len(shops):
        nav.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"reviews_page_{page + 1}"))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


# ─────────────────── МОИ МАГАЗИНЫ ───────────────────

async def create_my_shops_menu(user_id: int) -> InlineKeyboardMarkup:
    shops = await database.get_user_shops(user_id)
    builder = InlineKeyboardBuilder()
    for shop_id, shop_name in shops:
        builder.row(InlineKeyboardButton(text=shop_name, callback_data=f"manage_shop_{shop_id}"))
    builder.row(InlineKeyboardButton(text="➕ Создать новый магазин", callback_data="create_shop"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


# ─────────────────── УПРАВЛЕНИЕ МАГАЗИНОМ ───────────────────

def create_shop_management_menu(shop_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔑 API бота", callback_data=f"edit_token_{shop_id}"),
        InlineKeyboardButton(text="💳 PayMaster Токен", callback_data=f"paymaster_token_{shop_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="📦 Товары", callback_data=f"manage_products_{shop_id}"),
        InlineKeyboardButton(text="📦 Все товары", callback_data=f"all_products_{shop_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="👥 Работники", callback_data=f"workers_{shop_id}"),
        InlineKeyboardButton(text="📋 Заказы", callback_data=f"view_orders_{shop_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Рассылка", callback_data=f"broadcast_{shop_id}"),
        InlineKeyboardButton(text="🎟️ Промокоды", callback_data=f"manage_promocodes_{shop_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Способ оплаты", callback_data=f"payment_method_{shop_id}"),
        InlineKeyboardButton(text="👋 Приветствие", callback_data=f"edit_welcome_{shop_id}"),
    )
    builder.row(InlineKeyboardButton(text="🗑️ Удалить магазин", callback_data=f"delete_shop_{shop_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="my_shops"))
    return builder.as_markup()


# ─────────────────── ПАГИНАЦИЯ ОТЗЫВОВ (магазин-бот) ───────────────────

def create_shop_reviews_pagination(page: int, total_count: int, per_page: int = 5) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"reviews_prev_{page - 1}"))
    if (page + 1) * per_page < total_count:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"reviews_next_{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="💬 Оставить отзыв", callback_data="shop_leave_review"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_main_menu"))
    return builder.as_markup()


# ─────────────────── РАБОТНИКИ ───────────────────

def create_workers_menu(shop_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Добавить работника", callback_data=f"add_worker_{shop_id}"))
    builder.row(InlineKeyboardButton(text="📋 Список работников", callback_data=f"list_workers_{shop_id}"))
    builder.row(InlineKeyboardButton(text="➖ Уволить работника", callback_data=f"remove_worker_{shop_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return builder.as_markup()


def create_remove_worker_menu(shop_id: int, workers) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for worker_id, username in workers:
        text = f"@{username}" if username else f"ID: {worker_id}"
        builder.row(InlineKeyboardButton(
            text=text, callback_data=f"confirm_remove_{shop_id}_{worker_id}"
        ))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"workers_{shop_id}"))
    return builder.as_markup()


def create_confirm_remove_menu(shop_id: int, worker_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="┼ТОЧНО?┼", callback_data=f"confirm_remove_step2_{shop_id}_{worker_id}"
    ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"remove_worker_{shop_id}"))
    return builder.as_markup()


def create_confirm_remove_step2_menu(shop_id: int, worker_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="╤╧╨┼УВОЛИТЬ┼╨╧╤", callback_data=f"do_remove_{shop_id}_{worker_id}"
    ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"remove_worker_{shop_id}"))
    return builder.as_markup()


# ─────────────────── ЗАКАЗЫ ───────────────────

def create_orders_menu(shop_id: int, orders, page: int = 0, per_page: int = 5) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = min(start + per_page, len(orders))
    for order in orders[start:end]:
        order_id, _, product_name, quantity, total_price, _, status, _, username = order
        builder.row(InlineKeyboardButton(
            text=f"#{order_id} {product_name} x{quantity} - {status}",
            callback_data=f"order_detail_{order_id}"
        ))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"orders_page_{shop_id}_{page - 1}"))
    if end < len(orders):
        nav.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"orders_page_{shop_id}_{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data=f"view_orders_{shop_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return builder.as_markup()


# ─────────────────── КАТЕГОРИИ / ТОВАРЫ ───────────────────

async def create_categories_menu(shop_id: int) -> InlineKeyboardMarkup:
    categories = await database.get_shop_categories(shop_id)
    builder = InlineKeyboardBuilder()
    for category_id, category_name in categories:
        builder.row(InlineKeyboardButton(text=category_name, callback_data=f"category_{category_id}"))
    builder.row(InlineKeyboardButton(text="➕ Создать раздел", callback_data=f"create_category_{shop_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return builder.as_markup()


async def create_category_actions_menu(category_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📦 Просмотреть товары", callback_data=f"view_products_{category_id}"))
    builder.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_category_name_{category_id}"))
    builder.row(InlineKeyboardButton(text="🗑️ Удалить раздел", callback_data=f"delete_category_{category_id}"))
    shop_id = await database.get_shop_id_by_category(category_id)
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_products_{shop_id}"))
    return builder.as_markup()


async def create_products_menu(category_id: int, page: int = 0) -> InlineKeyboardMarkup:
    products = await database.get_category_products(category_id)
    builder = InlineKeyboardBuilder()
    start = page * 5
    end = min(start + 5, len(products))
    for product_id, name, price, image_path, description in products[start:end]:
        builder.row(InlineKeyboardButton(
            text=f"{name} - {price}₽",
            callback_data=f"product_{product_id}_{category_id}_{page}"
        ))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"prev_page_{category_id}_{page - 1}"))
    if end < len(products):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"next_page_{category_id}_{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data=f"add_product_{category_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"category_{category_id}"))
    return builder.as_markup()


def create_edit_product_menu(product_id: int, category_id: int, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_name_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"edit_price_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"edit_desc_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="🖼️ Изменить фото", callback_data=f"edit_photo_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="👁️ Показать товар", callback_data=f"show_product_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="🏷️ Скидка на товар", callback_data=f"edit_sale_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="🗑️ Удалить товар", callback_data=f"delete_product_{product_id}_{category_id}_{page}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_products_{category_id}_{page}"))
    return builder.as_markup()


# ─────────────────── ОБЩИЕ ───────────────────

def create_back_button_menu(target_callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=target_callback))
    return builder.as_markup()


# ─────────────────── ПРОМОКОДЫ ───────────────────

def create_promocodes_menu(shop_id: int, promos) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo_id, code, dtype, dvalue, max_uses, uses_count, is_active in promos:
        label = (f"{code} — -{int(dvalue)}%"
                 if dtype == 'percent'
                 else f"{code} — -{int(dvalue)}₽")
        uses_label = f"{uses_count}/{max_uses}" if max_uses else str(uses_count)
        builder.row(InlineKeyboardButton(
            text=f"{label}  ({uses_label} исп.)",
            callback_data=f"delete_promo_{promo_id}_{shop_id}"
        ))
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data=f"add_promocode_{shop_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return builder.as_markup()


def create_promo_type_menu(shop_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="% Процент", callback_data=f"promo_type_percent_{shop_id}"),
        InlineKeyboardButton(text="₽ Фиксированная", callback_data=f"promo_type_fixed_{shop_id}"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_promocodes_{shop_id}"))
    return builder.as_markup()