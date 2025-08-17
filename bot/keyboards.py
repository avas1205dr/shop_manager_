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
    btn_products = types.InlineKeyboardButton("📦 Товары", callback_data=f"manage_products_{shop_id}")
    btn_all_products = types.InlineKeyboardButton("📦 Все товары", callback_data=f"all_products_{shop_id}")
    btn_workers = types.InlineKeyboardButton("👥 Работники", callback_data=f"workers_{shop_id}")
    btn_delete = types.InlineKeyboardButton("🗑️ Удалить магазин", callback_data=f"delete_shop_{shop_id}")
    btn_back = types.InlineKeyboardButton("⬅️ Назад", callback_data="my_shops")
    
    markup.add(btn_token)
    markup.add(btn_products, btn_all_products)
    markup.add(btn_workers)
    btn_payment = types.InlineKeyboardButton("💳 Способ оплаты", callback_data=f"payment_method_{shop_id}")
    btn_welcome = types.InlineKeyboardButton("👋 Приветствие", callback_data=f"edit_welcome_{shop_id}")
    markup.add(btn_payment, btn_welcome)
    markup.add(btn_delete)
    markup.add(btn_back)
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
    markup.add(types.InlineKeyboardButton("🗑️ Удалить товар", 
              callback_data=f"delete_product_{product_id}_{category_id}_{page}"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", 
              callback_data=f"back_to_products_{category_id}_{page}"))
    return markup

def create_back_button_menu(target_callback):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=target_callback))
    return markup