import os
from pymongo import MongoClient

# MongoDB setup using environment variables
MONGO_HOST = os.getenv("MONGO_HOST", "mongodb://localhost:27017/")  # Default to localhost if not set
DB_NAME = os.getenv("DB_NAME", "email_database")  # Default to 'email_database' if not set

# Log message for connection creation
print(f"Connecting to MongoDB at {MONGO_HOST}, Database: {DB_NAME}")

# Create a shared MongoDB client and database instance
client = MongoClient(MONGO_HOST)
db = client[DB_NAME]
