"""POST /api/crawls to trigger a crawl for the E2E test identity.

Outputs the run_id to stdout and writes it to /tmp/crawl_run_id so the
checker container can pick it up from the shared volume.
"""

import hashlib
import json
import time
import urllib.error
import urllib.request

from pymongo import MongoClient

MONGO_URI = "mongodb://mongo:27017/"
API_HOST = "http://api:8080"
USER_USERNAME = "e2e-crawl-user"
USER_PASSWORD = "testpassword"
IDENTITY_NAME = "Crawl Test Identity"
_h = hashlib.sha256(USER_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()

# ── 1. Wait for MongoDB and resolve identity_id ──────────────────────────────

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

user_db = client[f"cover_letter_{USER_ID}"]
identity = user_db["identities"].find_one({"name": IDENTITY_NAME})
if not identity:
    raise SystemExit(f"Identity '{IDENTITY_NAME}' not found in MongoDB")

identity_id = str(identity["_id"])

# ── 2. Log in and get JWT ─────────────────────────────────────────────────────

token = None
login_deadline = time.time() + 60
while time.time() < login_deadline:
    try:
        req = urllib.request.Request(API_HOST + "/api/login", method="POST")
        req.add_header("Content-Type", "application/json")
        body = json.dumps({"username": USER_USERNAME, "password": USER_PASSWORD}).encode()
        with urllib.request.urlopen(req, data=body, timeout=5) as resp:
            parsed = json.loads(resp.read())
            token = parsed.get("token")
            if token:
                break
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Login failed: {exc.code} {exc.reason}")
    except urllib.error.URLError:
        time.sleep(0.5)

if not token:
    raise SystemExit("Login failed: API not reachable within deadline")

# ── 3. POST /api/crawls ───────────────────────────────────────────────────────

crawl_req = urllib.request.Request(API_HOST + "/api/crawls", method="POST")
crawl_req.add_header("Authorization", f"Bearer {token}")
crawl_req.add_header("Content-Type", "application/json")
payload = json.dumps({"identity_id": identity_id}).encode()

try:
    with urllib.request.urlopen(crawl_req, data=payload, timeout=10) as resp:
        if resp.status != 202:
            raise SystemExit(f"Expected 202, got {resp.status}")
        parsed = json.loads(resp.read())
        run_id = parsed.get("run_id")
        if not run_id:
            raise SystemExit(f"No run_id in response: {parsed}")
except urllib.error.HTTPError as exc:
    body = exc.read()
    raise SystemExit(f"POST /api/crawls failed: {exc.code} {exc.reason} {body.decode('utf-8', errors='replace')}")

# Write run_id to shared file for the checker
with open("/tmp/crawl_run_id", "w") as f:
    f.write(run_id)

print(f"PUSHED run_id={run_id} identity_id={identity_id}")
