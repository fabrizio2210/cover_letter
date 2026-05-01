"""
Seed fixture for the crawler scoring regression test.

Key distinction from seed_mongo.py: the global company is seeded WITHOUT
field_id and WITHOUT field. This replicates the real production state after the
mono-user → multi-user migration, where crawler-created companies have no field
linkage. The test verifies that the scorer can resolve identity directly via
identity_id in the queue payload instead of relying on the broken
company → field → identity inference chain.

Two job descriptions are seeded:
  JOB_ID_WITH_IDENTITY   — used to push a payload that includes identity_id
                            (happy path; should end up as scored).
  JOB_ID_WITHOUT_IDENTITY — used to push a payload without identity_id
                            (fail-fast path; should end up as skipped/failed).
"""

from pymongo import MongoClient
from bson.objectid import ObjectId
import hashlib
import os
import time

MONGO_URI = 'mongodb://mongo:27017/'

ADMIN_USERNAME = 'e2e-crawler-scoring-user'
_h = hashlib.sha256(ADMIN_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()

# Well-known fixed ObjectIDs so pusher and checker can reference them without
# needing to parse stdout from this script.
JOB_ID_WITH_IDENTITY = ObjectId('100000000000000000000001')
JOB_ID_WITHOUT_IDENTITY = ObjectId('100000000000000000000002')

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
    raise SystemExit('MongoDB not reachable')

# The e2e compose stack sets DB_NAME=cover_letter for ai_scorer.
# Keep seeding aligned with that runtime value.
global_db_name = os.environ.get('DB_NAME', 'cover_letter')
global_db = client[global_db_name]
user_db = client[f'cover_letter_{USER_ID}']

# Clean up any leftover data from previous runs
for col in ['job-descriptions', 'companies']:
    try:
        global_db[col].delete_many({
            '_id': {'$in': [
                JOB_ID_WITH_IDENTITY,
                JOB_ID_WITHOUT_IDENTITY,
            ]}
        })
    except Exception:
        pass
try:
    user_db['identities'].delete_many({'name': 'Crawler Scorer E2E Identity'})
except Exception:
    pass
for job_id_str in [str(JOB_ID_WITH_IDENTITY), str(JOB_ID_WITHOUT_IDENTITY)]:
    try:
        user_db['job-preference-scores'].delete_many({'job_id': job_id_str})
    except Exception:
        pass

# Company WITHOUT field_id — the broken production state this test exercises
company = {
    'name': 'Crawler E2E Corp',
    'canonical_name': 'crawler e2e corp',
    'description': 'Integration test company without field linkage',
    # Deliberately no 'field' and no 'field_id' keys
}
company_id = global_db['companies'].insert_one(company).inserted_id

# Per-user identity (lives in user DB, not global)
identity = {
    'name': 'Crawler Scorer E2E Identity',
    'description': 'Platform engineering identity for e2e regression test',
    'roles': ['Platform Engineer'],
    'preferences': [
        {'key': 'remote', 'guidance': 'Prefers fully remote work', 'weight': 2.0, 'enabled': True},
        {'key': 'backend', 'guidance': 'Backend infrastructure work', 'weight': 1.0, 'enabled': True},
    ],
}
identity_id = user_db['identities'].insert_one(identity).inserted_id

# Job used for the happy-path test (scorer gets identity_id in queue message)
job_with_identity = {
    '_id': JOB_ID_WITH_IDENTITY,
    'company': company_id,
    'company_id': company_id,
    'title': 'Senior Platform Engineer',
    'description': 'Build distributed systems and remote infrastructure for engineering teams.',
    'location': 'Remote',
    'platform': 'greenhouse',
    'external_job_id': 'crawler-e2e-with-identity',
    'created_at': {'seconds': 1704067200, 'nanos': 0},
    'updated_at': {'seconds': 1704067200, 'nanos': 0},
}
global_db['job-descriptions'].insert_one(job_with_identity)

# Job used for the fail-fast test (scorer gets no identity_id in queue message)
job_without_identity = {
    '_id': JOB_ID_WITHOUT_IDENTITY,
    'company': company_id,
    'company_id': company_id,
    'title': 'Backend Engineer',
    'description': 'Work on backend microservices.',
    'location': 'Hybrid',
    'platform': 'greenhouse',
    'external_job_id': 'crawler-e2e-without-identity',
    'created_at': {'seconds': 1704067200, 'nanos': 0},
    'updated_at': {'seconds': 1704067200, 'nanos': 0},
}
global_db['job-descriptions'].insert_one(job_without_identity)

print(f'SEEDED user_id={USER_ID} identity_id={identity_id} '
      f'job_with={JOB_ID_WITH_IDENTITY} job_without={JOB_ID_WITHOUT_IDENTITY}')
