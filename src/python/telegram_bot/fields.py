from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient
from telegram import Update
from telegram.ext import CallbackContext

# MongoDB setup
import os
MONGO_HOST = os.getenv("MONGO_HOST", "mongodb://localhost:27017/")  # Default to localhost if not set
DB_NAME = os.getenv("DB_NAME", "email_database")  # Default to 'email_database' if not set

client = MongoClient(MONGO_HOST)
db = client[DB_NAME]
fields_collection = db["fields"]  # Collection to store fields

def add_field(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Please send the field you want to add.")
    context.user_data["awaiting_field"] = True

def list_fields(update: Update, context: CallbackContext) -> None:
    fields = fields_collection.find()
    field_list = [field["field"] for field in fields]

    if field_list:
        update.message.reply_text("Fields in the database:\n\n" + "\n".join(field_list))
    else:
        update.message.reply_text("No fields found in the database.")

def remove_field(update: Update, context: CallbackContext) -> None:
    fields = fields_collection.find()
    field_list = [field["field"] for field in fields]

    if not field_list:
        update.message.reply_text("No fields found in the database to remove.")
        return

    keyboard = [
        [InlineKeyboardButton(field, callback_data=f"remove_field:{field}")]
        for field in field_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on a field to remove it:", reply_markup=reply_markup
    )

def process_field_callback(query, context: CallbackContext) -> bool:
    if query.data.startswith("remove_field:"):
        field_to_remove = query.data.split("remove_field:")[1]
        try:
            fields_collection.delete_one({"field": field_to_remove})
            query.edit_message_text(f"Field '{field_to_remove}' removed successfully.")
        except Exception as e:
            error_message = f"An error occurred while removing the field: {str(e)}"
            print(error_message)
            query.edit_message_text("Sorry, there was an error removing the field. Please try again later.")
        return True

    return False

def handle_field_message(update: Update, context: CallbackContext) -> bool:
    if context.user_data.get("awaiting_field"):
        field = update.message.text
        try:
            if fields_collection.find_one({"field": field}):
                update.message.reply_text("This field is already in the database.")
            else:
                fields_collection.insert_one({"field": field})
                update.message.reply_text(f"Field '{field}' added successfully.")
        except Exception as e:
            error_message = f"An error occurred while adding the field: {str(e)}"
            print(error_message)
            update.message.reply_text("Sorry, there was an error adding the field. Please try again later.")
        finally:
            context.user_data.pop("awaiting_field", None)  # Clear the flag
        return True
    return False
