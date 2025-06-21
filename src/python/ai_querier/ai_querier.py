import os
import redis
import time
import uuid
import json
from pymongo import MongoClient
from bson.objectid import ObjectId
import google.generativeai as genai


def main():
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    queue_name = os.environ.get("REDIS_QUEUE_GENERATE_EMAIL_NAME", "email_generation_queue")
    api_token = os.environ.get("GEMINI_TOKEN")
    if not api_token:
        raise RuntimeError(f"Environment variable for Gemini API token is not set.")
    genai.configure(api_key=api_token)

    # MongoDB setup
    mongo_uri = os.environ.get("MONGO_HOST", "mongodb://localhost:27017/")
    mongo_db_name = os.environ.get("DB_NAME", "cover_letter")
    client = MongoClient(mongo_uri)
    db = client[mongo_db_name]
    recipients_col = db["recipients"]
    identities_col = db["identities"]
    cover_letters_col = db["cover-letters"]

    r = redis.Redis(host=redis_host, port=redis_port)

    print(f"Listening for messages on Redis queue '{queue_name}'...")

    while True:
        try:
            msg = r.blpop(queue_name, timeout=0)
            if msg:
                _, data = msg
                try:
                    payload = json.loads(data.decode('utf-8'))
                except Exception as e:
                    print(f"Invalid JSON in queue message: {e}")
                    continue
                email = payload.get("recipient")
                conversation_id = payload.get("conversation_id")
                followup_prompt = payload.get("prompt")
                if not email:
                    print("No recipient specified in message.")
                    continue
                recipient = recipients_col.find_one({"email": email})
                if not recipient:
                    print(f"Recipient '{email}' not found in database.")
                    continue
                if not conversation_id:
                    generate_initial_cover_letter(recipient, identities_col, cover_letters_col)
                else:
                    iterate_cover_letter(email, conversation_id, followup_prompt, cover_letters_col)

        except Exception as e:
            print(f"Error while processing queue: {e}")
            time.sleep(5)

def process_cover_letter(cover_letters_col, recipient_id, cover_letter, prompt, history, conversation_id, is_update=False):
    now = time.time()
    if is_update:
        cover_letters_col.update_one(
            {"conversation_id": conversation_id},
            {"$set": {
                "cover_letter": cover_letter,
                "updated_at": now,
                "history": history,
                "prompt": prompt
            }}
        )
    else:
        cover_letters_col.insert_one({
            "recipient_id": recipient_id,
            "cover_letter": cover_letter,
            "created_at": now,
            "prompt": prompt,
            "history": history,
            "conversation_id": conversation_id
        })

def generate_initial_cover_letter(recipient, identities_col, cover_letters_col):
    field_id = recipient.get("field")
    if not field_id:
        print(f"No field associated with recipient '{recipient.get('email', '')}'.")
        return
    identity = identities_col.find_one({"field": ObjectId(field_id)})
    if not identity:
        print(f"No identity associated with field '{field_id}' for recipient '{recipient.get('email', '')}'.")
        return
    identity_name = identity.get("name", "No name")
    identity_description = identity.get("description", "No description")
    recipient_description = recipient.get("description", "No description")
    print(f"Identity for recipient '{recipient.get('email', '')}':")
    print(f"  Description: {recipient_description}")
    print(f"  For: {identity_name}")
    print(f"  Description: {identity_description}")
    prompt = (
        f"Write a cover letter for {identity_name} for {recipient}. "
        f"The {recipient} description is {recipient_description}. "
        f"{identity_name} is described with {identity_description}."
    )
    model = genai.GenerativeModel("gemini-1.5-flash")
    chat = model.start_chat(history=[])
    response = chat.send_message(prompt)
    if not response or not hasattr(response, "text"):
        print("No valid response from Gemini API.")
        return
    cover_letter = response.text.strip()
    conversation_id = str(uuid.uuid4())
    history = [
        {"role": "user", "content": prompt},
        {"role": "ai", "content": cover_letter}
    ]
    process_cover_letter(
        cover_letters_col,
        recipient["_id"],
        cover_letter,
        prompt,
        history,
        conversation_id,
        is_update=False
    )
    print(f"Cover letter for {recipient.get('email', '')} inserted into DB with conversation_id {conversation_id}.")

def iterate_cover_letter(email, conversation_id, followup_prompt, cover_letters_col):
    cover_letter_doc = cover_letters_col.find_one({"conversation_id": conversation_id})
    if not cover_letter_doc:
        print(f"No cover letter found for conversation_id {conversation_id}.")
        return
    history = cover_letter_doc.get("history", [])
    if not followup_prompt:
        print("No follow-up prompt provided for iteration.")
        return
    history.append({"role": "user", "content": followup_prompt})
    model = genai.GenerativeModel("gemini-1.5-flash")
    chat = model.start_chat(history=history)
    response = chat.send_message(followup_prompt)
    if not response or not hasattr(response, "text"):
        print("No valid response from Gemini API.")
        return
    new_cover_letter = response.text.strip()
    history.append({"role": "ai", "content": new_cover_letter})
    process_cover_letter(
        cover_letters_col,
        cover_letter_doc["recipient_id"],
        new_cover_letter,
        followup_prompt,
        history,
        conversation_id,
        is_update=True
    )
    print(f"Cover letter for {email} updated in DB for conversation_id {conversation_id}.")

if __name__ == "__main__":
    main()