from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient
from telegram import Update
from telegram.ext import CallbackContext
from bson.objectid import ObjectId  # Import ObjectId for MongoDB references

from src.python.telegram_bot.db import db  # Import shared db instance
from src.python.telegram_bot.fields import get_field_list  # Import get_field_list

identity_collection = db["identities"]  # Use shared db instance

def add_identity(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Please send the identity you want to add.")
    context.user_data["awaiting_identity"] = True

def list_identities(update: Update, context: CallbackContext) -> None:
    field_list = get_field_list()  # Retrieve all fields with their _id and names

    identities = identity_collection.find()
    identity_list = []

    for identity in identities:
        field_name = "No field associated"
        if "field" in identity:
            field_id = str(identity["field"])  # Convert ObjectId to string for comparison
            for field in field_list:
                if field["_id"] == field_id:
                    field_name = field["field"]
                    break

        identity_list.append(
            f"Identity: {identity['identity']}\n"
            f"Description: {identity.get('description', 'No description')}\n"
            f"Name: {identity.get('name', 'No name')}\n"
            f"Field: {field_name}"
        )

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

def associate_field_with_identity(update: Update, context: CallbackContext) -> None:
    identities = identity_collection.find()
    identity_list = [identity["identity"] for identity in identities]

    if not identity_list:
        update.message.reply_text("No identities found in the database to associate a field.")
        return

    field_list = get_field_list()  # Use updated get_field_list to retrieve fields with _id and name

    if not field_list:
        update.message.reply_text("No fields found in the database to associate with an identity.")
        return

    keyboard = [
        [InlineKeyboardButton(identity, callback_data=f"select_identity_for_field:{identity}")]
        for identity in identity_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an identity to associate a field to it:", reply_markup=reply_markup
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

    if query.data.startswith("select_identity_for_field:"):
        identity_to_update = query.data.split("select_identity_for_field:")[1]
        context.user_data["identity_to_update_for_field"] = identity_to_update

        # Use updated get_field_list to retrieve fields with _id and name
        field_list = get_field_list()
        keyboard = [
            [InlineKeyboardButton(field["field"], callback_data=f"associate_field:{field['_id']}")]
            for field in field_list
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        query.edit_message_text(
            f"Select a field to associate with identity: {identity_to_update}",
            reply_markup=reply_markup
        )
        return True

    if query.data.startswith("associate_field:"):
        field_id = query.data.split("associate_field:")[1]
        identity_to_update = context.user_data.pop("identity_to_update_for_field", None)

        if not identity_to_update:
            query.edit_message_text("Error: No identity selected for association.")
            return True

        try:
            # Store the field reference (_id) in the identity document
            identity_collection.update_one(
                {"identity": identity_to_update},
                {"$set": {"field": ObjectId(field_id)}}  # Use ObjectId for reference
            )
            query.edit_message_text(
                f"Field successfully associated with identity '{identity_to_update}'."
            )
        except Exception as e:
            error_message = f"An error occurred while associating the field: {str(e)}"
            print(error_message)
            query.edit_message_text("Sorry, there was an error associating the field. Please try again later.")
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
