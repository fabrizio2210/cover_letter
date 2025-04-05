import os
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from pymongo import MongoClient

# MongoDB setup using environment variables
MONGO_HOST = os.getenv("MONGO_HOST", "mongodb://localhost:27017/")  # Default to localhost if not set
DB_NAME = os.getenv("DB_NAME", "email_database")  # Default to 'email_database' if not set

client = MongoClient(MONGO_HOST)
db = client[DB_NAME]
collection = db["recipients"]  # Collection to store emails

# Read allowed Telegram user IDs from environment variable
ALLOWED_USERS_ENV = os.getenv("TELEGRAM_ALLOWED_USERS", "")  # Default to an empty string if not set
ALLOWED_USERS = {int(user_id) for user_id in ALLOWED_USERS_ENV.split(",") if user_id.strip().isdigit()}

# Decorator to restrict access to allowed users
def restricted(func):
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        print(f"User ID: {user_id}")  # Debugging line to check user ID
        if user_id not in ALLOWED_USERS:
            update.message.reply_text("Access denied. You are not authorized to use this bot.")
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# Command to add an email
@restricted
def add_email(update: Update, context: CallbackContext) -> None:
    if len(context.args) != 1:
        update.message.reply_text("Usage: /add_email <email>")
        return

    email = context.args[0]
    try:
        if collection.find_one({"email": email}):
            update.message.reply_text("This email is already in the database.")
        else:
            collection.insert_one({"email": email})
            update.message.reply_text(f"Email '{email}' added successfully.")
    except Exception as e:
        error_message = f"An error occurred while adding the email: {str(e)}"
        print(error_message)
        update.message.reply_text("Sorry, there was an error adding the email. Please try again later.")

# Command to list all emails
@restricted
def list_emails(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]
    if email_list:
        update.message.reply_text("Emails in the database:\n" + "\n".join(email_list))
    else:
        update.message.reply_text("No emails found in the database.")

# Command to remove an email
@restricted
def remove_email(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to remove.")
        return

    if len(context.args) == 0:
        # If no email is provided, show the list of emails
        email_options = "\n".join(f"{i + 1}. {email}" for i, email in enumerate(email_list))
        update.message.reply_text(
            "Please specify the email to remove by its number:\n" + email_options
        )
        return

    try:
        # Check if the user provided a valid number
        index = int(context.args[0]) - 1
        if index < 0 or index >= len(email_list):
            update.message.reply_text("Invalid number. Please try again.")
            return

        email_to_remove = email_list[index]
        collection.delete_one({"email": email_to_remove})
        update.message.reply_text(f"Email '{email_to_remove}' removed successfully.")
    except ValueError:
        update.message.reply_text("Invalid input. Please provide a valid number.")
    except Exception as e:
        error_message = f"An error occurred while removing the email: {str(e)}"
        print(error_message)
        update.message.reply_text("Sorry, there was an error removing the email. Please try again later.")

# Start command
@restricted
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Welcome! Use /add_email <email> to add an email, /list_emails to list all emails, and /remove_email to remove an email.")

def main():
    # Get the bot token from environment variables
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")

    updater = Updater(BOT_TOKEN)

    dispatcher = updater.dispatcher

    # Register commands
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("add_email", add_email))
    dispatcher.add_handler(CommandHandler("list_emails", list_emails))
    dispatcher.add_handler(CommandHandler("remove_email", remove_email))  # New command

    # Start the bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()