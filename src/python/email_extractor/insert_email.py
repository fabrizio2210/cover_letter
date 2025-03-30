import argparse
from pymongo import MongoClient

def insert_email(email, db_uri, db_name):
    # Connect to the MongoDB database
    client = MongoClient(db_uri)
    db = client[db_name]
    collection = db["recipients"]
    
    # Insert the email into the "recipients" collection if it doesn't already exist
    result = collection.update_one(
        {"email": email},  # Filter to check if the email already exists
        {"$setOnInsert": {"email": email}},  # Insert only if it doesn't exist
        upsert=True  # Perform an upsert operation
    )
    
    if result.upserted_id:
        print(f"Inserted email: {email}")
    else:
        print(f"Email already exists: {email}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Insert an email into MongoDB")
    parser.add_argument("--email", required=True, help="Email to insert")
    parser.add_argument("--db_uri", required=True, help="MongoDB connection URI")
    parser.add_argument("--db_name", required=True, help="MongoDB database name")
    
    args = parser.parse_args()
    
    insert_email(args.email, args.db_uri, args.db_name)
