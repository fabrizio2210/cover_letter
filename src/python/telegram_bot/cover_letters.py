import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from src.python.telegram_bot.db import db

collection = db["cover-letters"]

def select_cover_letter_for_recipient(update: Update, context: CallbackContext) -> None:
    from bson.objectid import ObjectId
    recipients_collection = db["recipients"]
    # Get all unique recipient_ids from cover-letters collection
    recipient_ids = collection.distinct("recipient_id")
    # Look up emails for these recipient_ids
    emails = []
    for rid in recipient_ids:
        recipient = recipients_collection.find_one({"_id": rid})
        if recipient and "email" in recipient:
            emails.append(recipient["email"])
    if not emails:
        update.message.reply_text("No recipients with cover letters found in the database.")
        return
    keyboard = [
        [InlineKeyboardButton(email, callback_data=f"list_cover_letters_for:{email}")]
        for email in emails
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Select a recipient to view their cover letters:", reply_markup=reply_markup
    )

def process_cover_letters_callback(query, context: CallbackContext) -> bool:
    if query.data.startswith("list_cover_letters_for:"):
        selected_email = query.data.split("list_cover_letters_for:")[1]
        from bson.objectid import ObjectId
        recipients_collection = db["recipients"]
        recipient = recipients_collection.find_one({"email": selected_email})
        if not recipient:
            query.edit_message_text("Recipient not found.")
            return True
        recipient_id = recipient["_id"]
        cover_letters = list(collection.find({"recipient_id": recipient_id}))
        if not cover_letters:
            query.edit_message_text(f"No cover letters found for {selected_email}.")
            return True
        keyboard = [
            [InlineKeyboardButton(
                f"{datetime.datetime.fromtimestamp(cl.get('created_at', 0)).strftime('%Y-%m-%d %H:%M:%S')}: {cl.get('cover_letter', '')[:30]}...",
                callback_data=f"show_cover_letter:{cl['_id']}"
            )]
            for cl in cover_letters
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(
            f"Select a cover letter for {selected_email}:", reply_markup=reply_markup
        )
        return True
    if query.data.startswith("show_cover_letter:"):
        from bson.objectid import ObjectId
        cover_letter_id = query.data.split("show_cover_letter:")[1]
        cover_letter_doc = collection.find_one({"_id": ObjectId(cover_letter_id)})
        if not cover_letter_doc:
            query.edit_message_text("Cover letter not found.")
            return True
        text = cover_letter_doc.get("cover_letter", "No content found.")
        query.edit_message_text(f"Cover letter:\n\n{text}")
        return True
    return False
