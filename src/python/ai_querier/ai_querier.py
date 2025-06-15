import os
import redis
import time
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

    r = redis.Redis(host=redis_host, port=redis_port)

    print(f"Listening for messages on Redis queue '{queue_name}'...")

    while True:
        try:
            msg = r.blpop(queue_name, timeout=0)
            if msg:
                _, data = msg
                email = data.decode('utf-8')
                print(f"Received email: {email}")

                # Step 1: Find recipient
                recipient = recipients_col.find_one({"email": email})
                if not recipient:
                    print(f"Recipient '{email}' not found in database.")
                    continue

                # Step 2: Get associated field
                field_id = recipient.get("field")
                if not field_id:
                    print(f"No field associated with recipient '{email}'.")
                    continue

                # Step 3: Find identity with this field
                identity = identities_col.find_one({"field": ObjectId(field_id)})
                if not identity:
                    print(f"No identity associated with field '{field_id}' for recipient '{email}'.")
                    continue

                # Step 4: Retrieve name and descriptions
                identity_name = identity.get("name", "No name")
                identity_description = identity.get("description", "No description")
                recipient_description = recipient.get("description", "No description")

                print(f"Identity for recipient '{email}':")
                print(f"  Description: {recipient_description}")
                print(f"  For: {identity_name}")
                print(f"  Description: {identity_description}")

                prompt = (
                    f"Write a cover letter for {identity_name} for {recipient}. "
                    f"The {recipient} description is {recipient_description}. "
                    f"{identity_name} is described with {identity_description}."
                )
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content(prompt)
                if not response or not hasattr(response, "text"):
                    print("No valid response from Gemini API.")
                    continue
                cover_letter = response.text.strip()
                print(f"Generated cover letter for {email}:\n{cover_letter}")

        except Exception as e:
            print(f"Error while processing queue: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
