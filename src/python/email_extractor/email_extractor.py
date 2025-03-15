import re
import requests
import argparse
from pymongo import MongoClient

def extract_emails(url):
    # Fetch the HTML content
    response = requests.get(url)
    html_content = response.text
    
    # Define the regular expression for email addresses
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    
    # Find all email addresses in the HTML content
    emails = re.findall(email_pattern, html_content)
    
    return emails

def insert_email(email, db_uri, db_name):
    # Connect to the MongoDB database
    client = MongoClient(db_uri)
    db = client[db_name]
    collection = db["recipients"]
    
    # Insert the email into the "recipients" collection
    collection.insert_one({"email": email})
    print(f"Inserted email: {email}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract emails from a URL and insert into MongoDB")
    parser.add_argument("--url", required=True, help="URL of the HTML page")
    parser.add_argument("--db_uri", required=True, help="MongoDB connection URI")
    parser.add_argument("--db_name", required=True, help="MongoDB database name")
    
    args = parser.parse_args()
    
    emails = extract_emails(args.url)
    print("Extracted emails:")
    for email in emails:
        print(email)
        insert_email(email, args.db_uri, args.db_name)
