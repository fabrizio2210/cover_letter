"""Seed MongoDB for the start-crawl E2E test.

Creates:
  - A field and company in the global DB (cover_letter)
  - An identity with roles in the per-user DB (cover_letter_<user_hash>)
"""

from pymongo import MongoClient
import hashlib
import time

MONGO_URI = "mongodb://mongo:27017/"
ADMIN_USERNAME = "e2e-crawl-user"
_h = hashlib.sha256(ADMIN_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()

IDENTITY_NAME = "Crawl Test Identity"

end = time.time() + 30
client = None
while time.time() < end:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.server_info()
        break
    except Exception:
        time.sleep(0.5)

if not client:
    raise SystemExit("MongoDB not reachable")

global_db = client["cover_letter"]
user_db = client[f"cover_letter_{USER_ID}"]

# Clean up any leftover data from previous runs
for c in ["fields", "companies"]:
    try:
        global_db.drop_collection(c)
    except Exception:
        pass
for c in ["identities"]:
    try:
        user_db.drop_collection(c)
    except Exception:
        pass

field_id = global_db["fields"].insert_one({"field": "Engineering"}).inserted_id

global_db["companies"].insert_one({
    "name": "CrawlTestCorp",
    "description": "Company for crawl E2E test",
    "field": field_id,
    "field_id": field_id,
})

identity_id = user_db["identities"].insert_one({
    "identity": "crawl-e2e-identity",
    "name": IDENTITY_NAME,
    "field": field_id,
    "field_id": field_id,
    "description": "Identity used by the start-crawl E2E test",
    "html_signature": "<p>crawl-test-sig</p>",
    "roles": ["backend", "remote"],
    "preferences": [
        {"key": "remote", "guidance": "Remote only", "weight": 2.0, "enabled": True},
        {"key": "backend", "guidance": "Backend engineering", "weight": 1.0, "enabled": True},
    ],
}).inserted_id

print(f"SEEDED identity_id={identity_id} user_id={USER_ID}")
