import telebot.types
from bot import config
import logging

import telebot

bot = telebot.TeleBot(config.BOT_TOKEN)

PRICE = telebot.types.LabeledPrice(label='Один тг бот в SHOPMANAGER', amount=1000000*100)

@bot.message_handler(commands=['start'])
def start_message(message):
    mk = telebot.types.InlineKeyboardMarkup(row_width=1)
    btn_pay = telebot.types.InlineKeyboardButton("Платить", callback_data="buy")
    mk.add(btn_pay)
    
    bot.send_message(message.chat.id, 'Здаров', reply_markup=mk)

@bot.message_handler(commands=['buy'])
def buy(message):
    bot.send_invoice(message.chat.id,
                     title='Бот-магазин 1шт',
                     description='Покупка одного тг-бота в конструкторе',
                     provider_token=config.PAYMENTS_TOKEN,
                     currency='rub',
                     is_flexible=False,
                     prices=[PRICE],
                     start_parameter='one-tg-bot',
                     invoice_payload='test-invoice-payload')
    
@bot.pre_checkout_query_handler(lambda query: True)
def pre_checkout_query(pre_checkout_q: telebot.types.PreCheckoutQuery):
    bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)
   
@bot.message_handler(content_types=['successful_payment'])
def succeful_payment(message: telebot.types.Message):
    print('SUCCEFUL PAYMENT: ')
    bot.send_message(message.chat.id, f'Платеж на сумму {message.successful_payment.total_amount // 100}')

bot.polling(non_stop=True)