import datetime
import os, redis, json
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
        # Store cover_letter_id for further actions
        context.user_data["cover_letter_id"] = str(cover_letter_id)
        # Inline keyboard for actions
        keyboard = [
            [
                InlineKeyboardButton("Refine", callback_data=f"refine_cover_letter:{cover_letter_id}"),
                InlineKeyboardButton("Send", callback_data=f"send_cover_letter:{cover_letter_id}"),
            ],
            [
                InlineKeyboardButton("Replace", callback_data=f"replace_cover_letter:{cover_letter_id}"),
                InlineKeyboardButton("Discard", callback_data=f"discard_cover_letter:{cover_letter_id}"),
            ],
            [
                InlineKeyboardButton("Cancel", callback_data=f"cancel_cover_letter:{cover_letter_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(f"Cover letter:\n\n{text}", reply_markup=reply_markup)
        return True
    if query.data.startswith("refine_cover_letter:"):
        cover_letter_id = query.data.split("refine_cover_letter:")[1]
        context.user_data["refine_cover_letter_id"] = cover_letter_id
        query.edit_message_text("Send your prompt to refine the cover letter.")
        return True
    if query.data.startswith("send_cover_letter:"):
        from bson.objectid import ObjectId
        cover_letter_id = query.data.split("send_cover_letter:")[1]
        cover_letter_doc = collection.find_one({"_id": ObjectId(cover_letter_id)})
        if not cover_letter_doc:
            query.edit_message_text("Cover letter not found.")
            return True
        recipients_collection = db["recipients"]
        recipient = recipients_collection.find_one({"_id": cover_letter_doc["recipient_id"]})
        if not recipient:
            query.edit_message_text("Recipient not found.")
            return True
        email = recipient["email"]
        cover_letter_text = cover_letter_doc.get("cover_letter", "")
        queue_name = os.environ.get("EMAILS_TO_SEND_QUEUE", "emails_to_send")
        r = redis.Redis(host=os.environ.get("REDIS_HOST", "localhost"), port=int(os.environ.get("REDIS_PORT", 6379)))
        payload = {"recipient": email, "cover_letter": cover_letter_text}
        r.rpush(queue_name, json.dumps(payload))
        query.edit_message_text(f"Cover letter sent to queue for {email}.")
        return True
    if query.data.startswith("cancel_cover_letter:"):
        query.edit_message_text("Action cancelled.")
        return True
    if query.data.startswith("discard_cover_letter:"):
        from bson.objectid import ObjectId
        cover_letter_id = query.data.split("discard_cover_letter:")[1]
        collection.delete_one({"_id": ObjectId(cover_letter_id)})
        query.edit_message_text("Cover letter discarded and removed from the database.")
        return True
    if query.data.startswith("replace_cover_letter:"):
        cover_letter_id = query.data.split("replace_cover_letter:")[1]
        context.user_data["replace_cover_letter_id"] = cover_letter_id
        query.edit_message_text("Send your version of the cover letter to replace the current one.")
        return True
    return False

def handle_cover_letter_message(update: Update, context: CallbackContext) -> bool:
    import os, redis, json, time
    from bson.objectid import ObjectId
    # Handle refine
    if context.user_data.get("refine_cover_letter_id"):
        cover_letter_id = context.user_data.pop("refine_cover_letter_id")
        cover_letter_doc = collection.find_one({"_id": ObjectId(cover_letter_id)})
        if not cover_letter_doc:
            update.message.reply_text("Cover letter not found.")
            return True
        recipients_collection = db["recipients"]
        recipient = recipients_collection.find_one({"_id": cover_letter_doc["recipient_id"]})
        if not recipient:
            update.message.reply_text("Recipient not found.")
            return True
        email = recipient["email"]
        conversation_id = cover_letter_doc.get("conversation_id")
        prompt = update.message.text
        queue_name = os.environ.get("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME", "cover_letter_generation_queue")
        r = redis.Redis(host=os.environ.get("REDIS_HOST", "localhost"), port=int(os.environ.get("REDIS_PORT", 6379)))
        payload = {"recipient": email, "conversation_id": conversation_id, "prompt": prompt}
        r.rpush(queue_name, json.dumps(payload))
        update.message.reply_text("Refinement prompt sent. The cover letter will be updated when ready.")
        return True
    # Handle replace
    if context.user_data.get("replace_cover_letter_id"):
        cover_letter_id = context.user_data.pop("replace_cover_letter_id")
        new_text = update.message.text
        now = time.time()
        collection.update_one({"_id": ObjectId(cover_letter_id)}, {"$set": {"cover_letter": new_text, "updated_at": now}})
        update.message.reply_text("Cover letter replaced successfully.")
        return True
    return False
