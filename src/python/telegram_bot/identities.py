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
identity_collection = db["identities"]  # Collection to store identities

def add_identity(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Please send the identity you want to add.")
    context.user_data["awaiting_identity"] = True

def list_identities(update: Update, context: CallbackContext) -> None:
    identities = identity_collection.find()
    identity_list = [
        f"Identity: {identity['identity']}\nDescription: {identity.get('description', 'No description')}\nName: {identity.get('name', 'No name')}"
        for identity in identities
    ]

    if identity_list:
        update.message.reply_text("Identities in the database:\n\n" + "\n\n".join(identity_list))
    else:
        update.message.reply_text("No identities found in the database.")

def add_identity_name(update: Update, context: CallbackContext) -> None:
    identities = identity_collection.find()
    identity_list = [identity["identity"] for identity in identities]

    if not identity_list:
        update.message.reply_text("No identities found in the database to add a name.")
        return

    keyboard = [
        [InlineKeyboardButton(identity, callback_data=f"add_identity_name:{identity}")]
        for identity in identity_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an identity to add a name to it:", reply_markup=reply_markup
    )

def add_identity_description(update: Update, context: CallbackContext) -> None:
    identities = identity_collection.find()
    identity_list = [identity["identity"] for identity in identities]

    if not identity_list:
        update.message.reply_text("No identities found in the database to add a description.")
        return

    keyboard = [
        [InlineKeyboardButton(identity, callback_data=f"add_identity_desc:{identity}")]
        for identity in identity_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an identity to add a description to it:", reply_markup=reply_markup
    )

def remove_identity(update: Update, context: CallbackContext) -> None:
    identities = identity_collection.find()
    identity_list = [identity["identity"] for identity in identities]

    if not identity_list:
        update.message.reply_text("No identities found in the database to remove.")
        return

    keyboard = [
        [InlineKeyboardButton(identity, callback_data=f"remove_identity:{identity}")]
        for identity in identity_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an identity to remove it:", reply_markup=reply_markup
    )

def process_identity_callback(query, context: CallbackContext) -> bool:
    if query.data.startswith("add_identity_desc:"):
        identity_to_update = query.data.split("add_identity_desc:")[1]
        context.user_data["identity_to_update"] = identity_to_update
        query.edit_message_text(f"Please send the description for the identity: {identity_to_update}")
        return True

    if query.data.startswith("add_identity_name:"):
        identity_to_update = query.data.split("add_identity_name:")[1]
        context.user_data["identity_to_update_for_name"] = identity_to_update
        query.edit_message_text(f"Please send the name for the identity: {identity_to_update}")
        return True

    if query.data.startswith("remove_identity:"):
        identity_to_remove = query.data.split("remove_identity:")[1]
        try:
            identity_collection.delete_one({"identity": identity_to_remove})
            query.edit_message_text(f"Identity '{identity_to_remove}' removed successfully.")
        except Exception as e:
            error_message = f"An error occurred while removing the identity: {str(e)}"
            print(error_message)
            query.edit_message_text("Sorry, there was an error removing the identity. Please try again later.")
        return True

    return False

def handle_identity_message(update: Update, context: CallbackContext) -> bool:
    if context.user_data.get("awaiting_identity"):
        identity = update.message.text
        try:
            if identity_collection.find_one({"identity": identity}):
                update.message.reply_text("This identity is already in the database.")
            else:
                identity_collection.insert_one({"identity": identity})
                update.message.reply_text(f"Identity '{identity}' added successfully.")
        except Exception as e:
            error_message = f"An error occurred while adding the identity: {str(e)}"
            print(error_message)
            update.message.reply_text("Sorry, there was an error adding the identity. Please try again later.")
        finally:
            context.user_data.pop("awaiting_identity", None)  # Clear the flag
        return True
    return False
