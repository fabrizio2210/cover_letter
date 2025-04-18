from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo.collection import Collection
from telegram import Update
from telegram.ext import CallbackContext
from src.python.telegram_bot.db import db  # Import shared db instance
from bson.objectid import ObjectId  # Import ObjectId for MongoDB references
from src.python.telegram_bot.fields import get_field_list  # Import get_field_list

collection = db["recipients"]  # Use shared db instance

def add_email_description(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to add a description.")
        return

    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"add_email_desc:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to add a description to it:", reply_markup=reply_markup
    )

def add_email_name(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to add a name.")
        return

    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"add_email_name:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to add a name to it:", reply_markup=reply_markup
    )

def add_email(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Please send the email address you want to add.")
    context.user_data["awaiting_email"] = True

def list_emails(update: Update, context: CallbackContext) -> None:
    field_list = get_field_list()  # Retrieve all fields with their _id and names

    emails = collection.find()
    email_list = []

    for email in emails:
        field_name = "No field associated"
        if "field" in email:
            field_id = str(email["field"])  # Convert ObjectId to string for comparison
            for field in field_list:
                if field["_id"] == field_id:
                    field_name = field["field"]
                    break

        email_list.append(
            f"Email: {email['email']}\n"
            f"Description: {email.get('description', 'No description')}\n"
            f"Name: {email.get('name', 'No name')}\n"
            f"Field: {field_name}"
        )

    if email_list:
        update.message.reply_text("Emails in the database:\n\n" + "\n\n".join(email_list))
    else:
        update.message.reply_text("No emails found in the database.")

def remove_email(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to remove.")
        return

    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"remove_email:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to remove it:", reply_markup=reply_markup
    )

def associate_email_with_field(update: Update, context: CallbackContext) -> None:
    emails = collection.find()
    email_list = [email["email"] for email in emails]

    if not email_list:
        update.message.reply_text("No emails found in the database to associate a field.")
        return

    field_list = get_field_list()  # Retrieve fields with _id and names

    if not field_list:
        update.message.reply_text("No fields found in the database to associate with an email.")
        return

    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"select_email_for_field:{email}")]
        for email in email_list
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "Click on an email to associate a field to it:", reply_markup=reply_markup
    )

def process_email_callback(query, context: CallbackContext) -> bool:
    if query.data.startswith("add_email_desc:"):
        email_to_update = query.data.split("add_email_desc:")[1]
        context.user_data["email_to_update"] = email_to_update
        query.edit_message_text(f"Please send the description for the email: {email_to_update}")
        return True

    if query.data.startswith("add_email_name:"):
        email_to_update = query.data.split("add_email_name:")[1]
        context.user_data["email_to_update_for_name"] = email_to_update
        query.edit_message_text(f"Please send the name for the email: {email_to_update}")
        return True

    if query.data.startswith("remove_email:"):
        email_to_remove = query.data.split("remove_email:")[1]
        try:
            collection.delete_one({"email": email_to_remove})
            query.edit_message_text(f"Email '{email_to_remove}' removed successfully.")
        except Exception as e:
            error_message = f"An error occurred while removing the email: {str(e)}"
            print(error_message)
            query.edit_message_text("Sorry, there was an error removing the email. Please try again later.")
        return True

    if query.data.startswith("select_email_for_field:"):
        email_to_update = query.data.split("select_email_for_field:")[1]
        context.user_data["email_to_update_for_field"] = email_to_update

        field_list = get_field_list()  # Retrieve fields with _id and names
        keyboard = [
            [InlineKeyboardButton(field["field"], callback_data=f"associate_field_to_email:{field['_id']}")]
            for field in field_list
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        query.edit_message_text(
            f"Select a field to associate with email: {email_to_update}",
            reply_markup=reply_markup
        )
        return True

    if query.data.startswith("associate_field_to_email:"):
        field_id = query.data.split("associate_field_to_email:")[1]
        email_to_update = context.user_data.pop("email_to_update_for_field", None)

        if not email_to_update:
            query.edit_message_text("Error: No email selected for association.")
            return True

        try:
            # Store the field reference (_id) in the email document
            collection.update_one(
                {"email": email_to_update},
                {"$set": {"field": ObjectId(field_id)}}  # Use ObjectId for reference
            )
            query.edit_message_text(
                f"Field successfully associated with email '{email_to_update}'."
            )
        except Exception as e:
            error_message = f"An error occurred while associating the field: {str(e)}"
            print(error_message)
            query.edit_message_text("Sorry, there was an error associating the field. Please try again later.")
        return True

    return False

def handle_email_message(update: Update, context: CallbackContext) -> bool:
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
        return True
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
        return True
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
        return True
    return False  # No action taken