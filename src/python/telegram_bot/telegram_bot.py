import os
import sys

# Add the 'src' directory to sys.path for absolute imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from telegram import BotCommand, Update
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, Filters
from src.python.telegram_bot.recipients import (
    add_email_description,
    add_email_name,
    add_email,
    list_emails,
    remove_email,
    process_recipients_callback,
    handle_recipients_message,
    associate_email_with_field,
    select_recipient_for_generation,
)
from src.python.telegram_bot.identities import (
    add_identity,
    list_identities,
    add_identity_name,
    add_identity_description,
    remove_identity,
    process_identity_callback,
    handle_identity_message,
    associate_field_with_identity,
)
from src.python.telegram_bot.fields import (
    add_field,
    list_fields,
    remove_field,
    process_field_callback,
    handle_field_message,
)
from src.python.telegram_bot.db import db  # Import shared db instance
# Remove process_email_callback from imports

# Read allowed Telegram user IDs from environment variable
ALLOWED_USERS_ENV = os.getenv("TELEGRAM_ALLOWED_USERS", "")  # Default to an empty string if not set
ALLOWED_USERS = {int(user_id) for user_id in ALLOWED_USERS_ENV.split(",") if user_id.strip().isdigit()}

# Decorator to restrict access to allowed users
def restricted(func):
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        print(f"User ID: {user_id}, Function: {func.__name__}")  # Log user ID and function name
        if user_id not in ALLOWED_USERS:
            update.message.reply_text("Access denied. You are not authorized to use this bot.")
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# Unified message handler to handle both email and description inputs
@restricted
def handle_message(update: Update, context: CallbackContext) -> None:
    # Delegate to email-related message processing
    if handle_recipients_message(update, context):
        return

    # Delegate to identity-related message processing
    if handle_identity_message(update, context):
        return

    # Delegate to field-related message processing
    if handle_field_message(update, context):
        return

    # Default response for unexpected messages
    update.message.reply_text("I didn't understand that. Please use one of the commands.")

@restricted
def handle_callback_query_command(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()

    # Delegate recipients-related callback queries to recipients.py
    if process_recipients_callback(query, context):
        return

    # Delegate identity-related callback queries to identities.py
    if process_identity_callback(query, context):
        return

    # Delegate field-related callback queries to fields.py
    if process_field_callback(query, context):
        return

    # If no specific processing was done, you can handle other callback queries here
    # ...handle other callback queries if needed...

@restricted
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Welcome! Use the menu on the side.")

def main():
    # Log application start
    print("Telegram bot application is starting...")

    # Get the bot token from environment variables
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")

    updater = Updater(BOT_TOKEN)

    dispatcher = updater.dispatcher

    # Register commands for emails
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("add_email", restricted(add_email)))
    dispatcher.add_handler(CommandHandler("list_emails", restricted(list_emails)))
    dispatcher.add_handler(CommandHandler("remove_email", restricted(remove_email)))
    dispatcher.add_handler(CommandHandler("add_email_description", restricted(add_email_description)))
    dispatcher.add_handler(CommandHandler("add_email_name", restricted(add_email_name)))
    dispatcher.add_handler(CommandHandler("associate_email_with_field", restricted(associate_email_with_field)))

    # Register commands for identities
    dispatcher.add_handler(CommandHandler("add_identity", restricted(add_identity)))
    dispatcher.add_handler(CommandHandler("list_identities", restricted(list_identities)))
    dispatcher.add_handler(CommandHandler("remove_identity", restricted(remove_identity)))
    dispatcher.add_handler(CommandHandler("add_identity_description", restricted(add_identity_description)))
    dispatcher.add_handler(CommandHandler("add_identity_name", restricted(add_identity_name)))
    dispatcher.add_handler(CommandHandler("associate_field_with_identity", restricted(associate_field_with_identity)))

    # Register commands for fields
    dispatcher.add_handler(CommandHandler("add_field", restricted(add_field)))
    dispatcher.add_handler(CommandHandler("list_fields", restricted(list_fields)))
    dispatcher.add_handler(CommandHandler("remove_field", restricted(remove_field)))

    # Register command for generating email
    dispatcher.add_handler(CommandHandler("select_recipient_for_generation", restricted(select_recipient_for_generation)))

    # Register callback query handler
    dispatcher.add_handler(CallbackQueryHandler(handle_callback_query_command))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))  # Unified message handler

    # Set bot commands using setMyCommands
    updater.bot.set_my_commands([
        BotCommand("start", "Start the bot and see a welcome message"),
        BotCommand("add_email", "Add an email to the database"),
        BotCommand("list_emails", "List all emails in the database"),
        BotCommand("remove_email", "Remove an email from the database using an interactive list"),
        BotCommand("select_recipient_for_generation", "Select a recipient to generate an email for"),
        BotCommand("add_email_description", "Add a description to an email"),
        BotCommand("add_email_name", "Add a name to an email"),
        BotCommand("associate_email_with_field", "Associate a field to an email"),
        BotCommand("add_identity", "Add an identity to the database"),
        BotCommand("list_identities", "List all identities in the database"),
        BotCommand("remove_identity", "Remove an identity from the database using an interactive list"),
        BotCommand("add_identity_description", "Add a description to an identity"),
        BotCommand("add_identity_name", "Add a name to an identity"),
        BotCommand("associate_field_with_identity", "Associate a field with an identity"),
        BotCommand("add_field", "Add a field to the database"),
        BotCommand("list_fields", "List all fields in the database"),
        BotCommand("remove_field", "Remove a field from the database using an interactive list"),
    ])

    # Start the bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()