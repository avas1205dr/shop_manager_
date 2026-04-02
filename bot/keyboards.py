from telebot import types
import database as database

def create_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_reviews = types.InlineKeyboardButton("📊 Рейтинг", callback_data="reviews")
    btn_my_shops = types.InlineKeyboardButton("🏪 Мои магазины", callback_data="my_shops")
    markup.add(btn_reviews, btn_my_shops)
    return markup

def create_reviews_menu(page=0, per_page=5):
    markup = types.InlineKeyboardMarkup(row_width=1)
    shops = database.get_shops_with_ratings()
    
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(shops))
    
    for i in range(start_idx, end_idx):
        shop = shops[i]
        shop_id, shop_name, bot_username, avg_rating, review_count = shop
        avg_rating = float(avg_rating or 0)
        
        rating_stars = "⭐" * int(avg_rating) if avg_rating > 0 else "Нет оценок"
        
        btn_text = f"{shop_name}\n{rating_stars} ({review_count} отзывов)"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"shop_detail_{shop_id}"))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"reviews_page_{page-1}"))
    if end_idx < len(shops):
        nav_buttons.append(types.InlineKeyboardButton("Вперёд ➡️", callback_data=f"reviews_page_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return markup

def create_my_shops_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    shops = database.get_user_shops(user_id)
    
    for shop_id, shop_name in shops:
        markup.add(types.InlineKeyboardButton(shop_name, callback_data=f"manage_shop_{shop_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Создать новый магазин", callback_data="create_shop"))
    markup.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return markup

def create_shop_management_menu(shop_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_token = types.InlineKeyboardButton("🔑 API бота", callback_data=f"edit_token_{shop_id}")
    btn_paymaster = types.InlineKeyboardButton("💳 PayMaster Токен", callback_data=f"paymaster_token_{shop_id}")
    btn_products = types.InlineKeyboardButton("📦 Товары", callback_data=f"manage_products_{shop_id}")
    btn_all_products = types.InlineKeyboardButton("📦 Все товары", callback_data=f"all_products_{shop_id}")
    btn_orders = types.InlineKeyboardButton("📋 Заказы", callback_data=f"view_orders_{shop_id}")
    btn_workers = types.InlineKeyboardButton("👥 Работники", callback_data=f"workers_{shop_id}")
    btn_broadcast = types.InlineKeyboardButton("📢 Рассылка", callback_data=f"broadcast_{shop_id}") # НОВАЯ КНОПКА
    btn_promo = types.InlineKeyboardButton("🎟️ Промокоды", callback_data=f"manage_promocodes_{shop_id}")
    btn_delete = types.InlineKeyboardButton("🗑️ Удалить магазин", callback_data=f"delete_shop_{shop_id}")
    btn_back = types.InlineKeyboardButton("⬅️ Назад", callback_data="my_shops")
    
    markup.add(btn_token, btn_paymaster)
    markup.add(btn_products, btn_all_products)
    markup.add(btn_workers, btn_orders)
    markup.add(btn_broadcast, btn_promo)
    btn_payment = types.InlineKeyboardButton("💳 Способ оплаты", callback_data=f"payment_method_{shop_id}")
    btn_welcome = types.InlineKeyboardButton("👋 Приветствие", callback_data=f"edit_welcome_{shop_id}")
    markup.add(btn_payment, btn_welcome)
    markup.add(btn_delete)
    markup.add(btn_back)
    return markup

def create_shop_reviews_pagination(page, total_count, per_page=5):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    
    if page > 0:
        buttons.append(types.InlineKeyboardButton("⬅️", callback_data=f"reviews_prev_{page-1}"))
    
    if (page + 1) * per_page < total_count:
        buttons.append(types.InlineKeyboardButton("➡️", callback_data=f"reviews_next_{page+1}"))
        
    if buttons:
        markup.row(*buttons)
        
    markup.add(types.InlineKeyboardButton("💬 Оставить отзыв", callback_data="shop_leave_review"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="shop_main_menu"))
    return markup

def create_workers_menu(shop_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Добавить работника", callback_data=f"add_worker_{shop_id}"))
    markup.add(types.InlineKeyboardButton("📋 Список работников", callback_data=f"list_workers_{shop_id}"))
    markup.add(types.InlineKeyboardButton("➖ Уволить работника", callback_data=f"remove_worker_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return markup

def create_remove_worker_menu(shop_id, workers):
    markup = types.InlineKeyboardMarkup()
    for worker_id, username in workers:
        worker_text = f"@{username}" if username else f"ID: {worker_id}"
        markup.add(types.InlineKeyboardButton(worker_text, callback_data=f"confirm_remove_{shop_id}_{worker_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"workers_{shop_id}"))
    return markup

def create_confirm_remove_menu(shop_id, worker_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("┼ТОЧНО?┼", callback_data=f"confirm_remove_step2_{shop_id}_{worker_id}"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"remove_worker_{shop_id}"))
    return markup

def create_confirm_remove_step2_menu(shop_id, worker_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("╤╧╨┼УВОЛИТЬ┼╨╧╤", callback_data=f"do_remove_{shop_id}_{worker_id}"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"remove_worker_{shop_id}"))
    return markup

def create_orders_menu(shop_id, orders, page=0, per_page=5):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(orders))
    
    for i in range(start_idx, end_idx):
        order = orders[i]
        order_id, _, product_name, quantity, total_price, _, status, _, username = order
        btn_text = f"#{order_id} {product_name} x{quantity} - {status}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"order_detail_{order_id}"))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"orders_page_{shop_id}_{page-1}"))
    if end_idx < len(orders):
        nav_buttons.append(types.InlineKeyboardButton("Вперёд ➡️", callback_data=f"orders_page_{shop_id}_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data=f"view_orders_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return markup

def create_categories_menu(shop_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    categories = database.get_shop_categories(shop_id)
    
    for category_id, category_name in categories:
        markup.add(types.InlineKeyboardButton(category_name, callback_data=f"category_{category_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Создать раздел", callback_data=f"create_category_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return markup

def create_category_actions_menu(category_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("📦 Просмотреть товары", callback_data=f"view_products_{category_id}"))
    markup.add(types.InlineKeyboardButton("✏️ Изменить название", callback_data=f"edit_category_name_{category_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ Удалить раздел", callback_data=f"delete_category_{category_id}"))
    shop_id = database.get_shop_id_by_category(category_id)
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_products_{shop_id}"))
    return markup

def create_products_menu(category_id, page=0):
    markup = types.InlineKeyboardMarkup(row_width=1)
    products = database.get_category_products(category_id)
    
    start_idx = page * 5
    end_idx = min(start_idx + 5, len(products))
    
    for i in range(start_idx, end_idx):
        product = products[i]
        product_id, name, price, image_path, description = product
        markup.add(types.InlineKeyboardButton(f"{name} - {price}₽", callback_data=f"product_{product_id}_{category_id}_{page}"))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️", callback_data=f"prev_page_{category_id}_{page-1}"))
    if end_idx < len(products):
        nav_buttons.append(types.InlineKeyboardButton("➡️", callback_data=f"next_page_{category_id}_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.add(types.InlineKeyboardButton("➕ Добавить товар", callback_data=f"add_product_{category_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"category_{category_id}"))
    return markup

def create_edit_product_menu(product_id, category_id, page=0):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✏️ Изменить название", 
              callback_data=f"edit_name_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("💰 Изменить цену", 
              callback_data=f"edit_price_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("📝 Изменить описание", 
              callback_data=f"edit_desc_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🖼️ Изменить фото", 
              callback_data=f"edit_photo_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("👁️ Показать товар", 
              callback_data=f"show_product_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🏷️ Скидка на товар", 
              callback_data=f"edit_sale_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🗑️ Удалить товар", 
              callback_data=f"delete_product_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", 
              callback_data=f"back_to_products_{category_id}_{page}"))
    return markup

def create_back_button_menu(target_callback):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=target_callback))
    return markup

def create_promocodes_menu(shop_id, promos):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for promo_id, code, dtype, dvalue, max_uses, uses_count, is_active in promos:
        label = f"{code} — {'−' + str(int(dvalue)) + '%' if dtype == 'percent' else '−' + str(int(dvalue)) + '₽'}"
        uses_label = f"{uses_count}/{max_uses}" if max_uses else str(uses_count)
        markup.add(types.InlineKeyboardButton(
            f"{label}  ({uses_label} исп.)",
            callback_data=f"delete_promo_{promo_id}_{shop_id}"
        ))
    markup.add(types.InlineKeyboardButton("➕ Создать промокод", callback_data=f"add_promocode_{shop_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_shop_{shop_id}"))
    return markup

def create_promo_type_menu(shop_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("% Процент", callback_data=f"promo_type_percent_{shop_id}"),
        types.InlineKeyboardButton("₽ Фиксированная", callback_data=f"promo_type_fixed_{shop_id}")
    )
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_promocodes_{shop_id}"))
    return markup