import argparse
from pymongo import MongoClient

def insert_email(email, db_uri, db_name):
    # Connect to the MongoDB database
    client = MongoClient(db_uri)
    db = client[db_name]
    collection = db["recipients"]
    
    # Insert the email into the "recipients" collection
    collection.insert_one({"email": email})
    print(f"Inserted email: {email}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Insert an email into MongoDB")
    parser.add_argument("--email", required=True, help="Email to insert")
    parser.add_argument("--db_uri", required=True, help="MongoDB connection URI")
    parser.add_argument("--db_name", required=True, help="MongoDB database name")
    
    args = parser.parse_args()
    
    insert_email(args.email, args.db_uri, args.db_name)
