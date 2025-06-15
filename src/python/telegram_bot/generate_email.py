import os
import redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from src.python.telegram_bot.db import db

collection = db["recipients"]

def select_recipient_for_generation(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No recipients found in the database to generate an email for.")
        return

    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"select_recipient:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Select a recipient to generate an email for:", reply_markup=reply_markup
    )

def process_email_callback(query, context: CallbackContext) -> bool:
    if query.data.startswith("select_recipient:"):
        recipient_email = query.data.split("select_recipient:")[1]
        context.user_data["selected_recipient"] = recipient_email
        # Enqueue the selected recipient for email generation
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = int(os.environ.get("REDIS_PORT", 6379))
        queue_name = os.environ.get("REDIS_QUEUE_GENERATE_EMAIL_NAME", "email_generation_queue")
        try:
            r = redis.Redis(host=redis_host, port=redis_port)
            r.rpush(queue_name, recipient_email)
            query.edit_message_text(f"Recipient '{recipient_email}' added to the email generation queue.")
        except Exception as e:
            print(f"Redis error: {e}")
            query.edit_message_text("Failed to add recipient to the queue. Please try again later.")
        return True
    return False
