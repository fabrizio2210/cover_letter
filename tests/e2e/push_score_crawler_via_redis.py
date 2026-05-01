"""
Push two scoring queue messages directly to Redis for the crawler scoring
regression test.

  Message A (happy path):  {user_id, job_id, identity_id}
    The scorer must resolve identity by _id directly — it cannot use the
    company → field → identity chain because the company has no field_id.
    Expected outcome: scoring_status = "scored".

  Message B (fail-fast):   {user_id, job_id}  — no identity_id
    The scorer must not silently skip with a misleading identity_not_found;
    it must emit an explicit terminal status.
    Expected outcome: scoring_status = "skipped" or "failed".
"""

import hashlib
import json
import time
import redis
from pymongo import MongoClient

REDIS_HOST = 'redis'
REDIS_PORT = 6379
QUEUE_NAME = 'job_scoring_queue'
MONGO_URI = 'mongodb://mongo:27017/'

ADMIN_USERNAME = 'e2e-crawler-scoring-user'
_h = hashlib.sha256(ADMIN_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()

JOB_ID_WITH_IDENTITY = '100000000000000000000001'
JOB_ID_WITHOUT_IDENTITY = '100000000000000000000002'

# Connect to MongoDB to look up the seeded identity_id
mongo_end = time.time() + 30
mongo_client = None
while time.time() < mongo_end:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        mongo_client.server_info()
        break
    except Exception:
        time.sleep(0.5)

if not mongo_client:
    raise SystemExit('MongoDB not reachable')

user_db = mongo_client[f'cover_letter_{USER_ID}']
identity_doc = user_db['identities'].find_one({'name': 'Crawler Scorer E2E Identity'})
if not identity_doc:
    raise SystemExit(f'Seeded identity not found in cover_letter_{USER_ID}.identities')
identity_id = str(identity_doc['_id'])

# Connect to Redis with retry
end = time.time() + 30
client = None
while time.time() < end:
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=3)
        client.ping()
        break
    except Exception:
        time.sleep(0.5)

if not client:
    raise SystemExit('Redis not reachable')

# Message A — happy path: includes identity_id
msg_a = json.dumps({
    'user_id': USER_ID,
    'job_id': JOB_ID_WITH_IDENTITY,
    'identity_id': identity_id,
})
client.rpush(QUEUE_NAME, msg_a)

# Message B — fail-fast: no identity_id, company has no field_id
msg_b = json.dumps({
    'user_id': USER_ID,
    'job_id': JOB_ID_WITHOUT_IDENTITY,
})
client.rpush(QUEUE_NAME, msg_b)

print(f'PUSHED user_id={USER_ID} identity_id={identity_id}')
print(f'  msg_a job_id={JOB_ID_WITH_IDENTITY} (with identity_id)')
print(f'  msg_b job_id={JOB_ID_WITHOUT_IDENTITY} (without identity_id)')
