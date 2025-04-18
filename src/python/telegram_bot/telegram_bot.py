import os
from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, Filters
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

# Command to add a description to an email
@restricted
def add_email_description(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to add a description.")
        return

    # Create an inline keyboard with email options
    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"add_email_desc:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to add a description to it:", reply_markup=reply_markup
    )

# Command to add a name to an email
@restricted
def add_email_name(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to add a name.")
        return

    # Create an inline keyboard with email options
    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"add_email_name:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to add a name to it:", reply_markup=reply_markup
    )

# Command to initiate adding an email
@restricted
def add_email(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Please send the email address you want to add.")
    context.user_data["awaiting_email"] = True  # Set a flag to indicate we're waiting for the email

# Command to list all emails with their descriptions
@restricted
def list_emails(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [
        f"Email: {email['email']}\nDescription: {email.get('description', 'No description')}\nName: {email.get('name', 'No name')}"
        for email in emails
    ]

    if email_list:
        update.message.reply_text("Emails in the database:\n\n" + "\n\n".join(email_list))
    else:
        update.message.reply_text("No emails found in the database.")

# Command to remove an email using an interactive list
@restricted
def remove_email(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to remove.")
        return

    # Create an inline keyboard with email options
    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"remove:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to remove it:", reply_markup=reply_markup
    )

@restricted
def handle_callback_query(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    if query.data.startswith("add_email_desc:"):
        # Extract the email from the callback data
        email_to_update = query.data.split("add_email_desc:")[1]

        # Ask the user to provide a description
        context.user_data["email_to_update"] = email_to_update
        query.edit_message_text(f"Please send the description for the email: {email_to_update}")

    if query.data.startswith("add_email_name:"):
        # Extract the email from the callback data
        email_to_update = query.data.split("add_email_name:")[1]

        # Ask the user to provide a name
        context.user_data["email_to_update_for_name"] = email_to_update
        query.edit_message_text(f"Please send the name for the email: {email_to_update}")

    if query.data.startswith("remove:"):
        # Handle email removal actions
        email_to_remove = query.data.split("remove:")[1]
        try:
            collection.delete_one({"email": email_to_remove})
            query.edit_message_text(f"Email '{email_to_remove}' removed successfully.")
        except Exception as e:
            error_message = f"An error occurred while removing the email: {str(e)}"
            print(error_message)
            query.edit_message_text("Sorry, there was an error removing the email. Please try again later.")

# Unified message handler to handle both email and description inputs
@restricted
def handle_message(update: Update, context: CallbackContext) -> None:
    # Check if the bot is waiting for an email
    if context.user_data.get("awaiting_email"):
        email = update.message.text
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
        finally:
            context.user_data.pop("awaiting_email", None)  # Clear the flag
        return

    # Check if the bot is waiting for a description
    if context.user_data.get("email_to_update"):
        email_to_update = context.user_data["email_to_update"]
        description = update.message.text
        try:
            # Update the email with the description in the database
            collection.update_one({"email": email_to_update}, {"$set": {"description": description}})
            update.message.reply_text(f"Description added to email '{email_to_update}': {description}")
            context.user_data.pop("email_to_update", None)  # Clear the stored email
        except Exception as e:
            error_message = f"An error occurred while adding the description: {str(e)}"
            print(error_message)
            update.message.reply_text("Sorry, there was an error adding the description. Please try again later.")
        return

    # Check if the bot is waiting for a name
    if context.user_data.get("email_to_update_for_name"):
        email_to_update = context.user_data["email_to_update_for_name"]
        name = update.message.text
        try:
            # Update the email with the name in the database
            collection.update_one({"email": email_to_update}, {"$set": {"name": name}})
            update.message.reply_text(f"Name '{name}' added to email '{email_to_update}'.")
            context.user_data.pop("email_to_update_for_name", None)  # Clear the stored email
        except Exception as e:
            error_message = f"An error occurred while adding the name: {str(e)}"
            print(error_message)
            update.message.reply_text("Sorry, there was an error adding the name. Please try again later.")
        return

    # Default response for unexpected messages
    update.message.reply_text("I didn't understand that. Please use one of the commands.")

@restricted
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Welcome! Use the menu on the side.")

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
    dispatcher.add_handler(CommandHandler("remove_email", remove_email))
    dispatcher.add_handler(CommandHandler("add_email_description", add_email_description))
    dispatcher.add_handler(CommandHandler("add_email_name", add_email_name))

    # Register callback query handler
    dispatcher.add_handler(CallbackQueryHandler(handle_callback_query))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))  # Unified message handler

    # Set bot commands using setMyCommands
    updater.bot.set_my_commands([
        BotCommand("start", "Start the bot and see a welcome message"),
        BotCommand("add_email", "Add an email to the database"),
        BotCommand("list_emails", "List all emails in the database"),
        BotCommand("remove_email", "Remove an email from the database using an interactive list"),
        BotCommand("add_email_description", "Add a description to an email"),
        BotCommand("add_email_name", "Add a name to an email"),
    ])

    # Start the bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()