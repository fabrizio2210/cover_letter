import os
import redis
import time
from pymongo import MongoClient
from bson.objectid import ObjectId

def main():
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    queue_name = os.environ.get("REDIS_QUEUE_GENERATE_EMAIL_NAME", "email_generation_queue")

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

                # Step 4: Retrieve name and description
                identity_name = identity.get("name", "No name")
                identity_description = identity.get("description", "No description")

                print(f"Identity for recipient '{email}':")
                print(f"  Name: {identity_name}")
                print(f"  Description: {identity_description}")

                # ...further processing as needed...

        except Exception as e:
            print(f"Error while processing queue: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
