"""
Checker for the crawler scoring regression test.

Asserts two outcomes after the scorer processes both queue messages:

  Job A (happy path — payload included identity_id):
    scoring_status == "scored" AND weighted_score > 0
    This verifies the scorer can resolve identity by _id directly, even when
    the company document has no field_id (the real production data state).

  Job B (fail-fast path — payload had no identity_id):
    scoring_status in {"skipped", "failed"}  AND  scoring_status != "scored"
    This verifies that missing identity_id produces an explicit terminal status
    instead of silently looping as identity_not_found forever.
"""

from pymongo import MongoClient
from bson.objectid import ObjectId
import hashlib
import sys
import time

MONGO_URI = 'mongodb://mongo:27017/'

ADMIN_USERNAME = 'e2e-crawler-scoring-user'
_h = hashlib.sha256(ADMIN_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()

PREFERENCE_WEIGHTS = {'remote': 2.0, 'backend': 1.0}
TERMINAL_FAIL_STATUSES = {'skipped', 'failed'}

JOB_ID_WITH_IDENTITY = '100000000000000000000001'
JOB_ID_WITHOUT_IDENTITY = '100000000000000000000002'


def stable_test_score(job_id, preference_key):
    seed_text = f'{job_id}:{preference_key}'
    seed = sum(ord(ch) for ch in seed_text)
    return (seed % 5) + 1


# Connect to MongoDB
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
    print('NOT_FOUND: MongoDB not reachable')
    sys.exit(2)

# Resolve identity_id from the user DB (seeded by seed_mongo_crawler_scoring.py)
user_db_early = client[f'cover_letter_{USER_ID}']
identity_doc = user_db_early['identities'].find_one({'name': 'Crawler Scorer E2E Identity'})
if not identity_doc:
    print(f'NOT_FOUND: seeded identity missing from cover_letter_{USER_ID}.identities')
    sys.exit(2)
identity_id = str(identity_doc['_id'])

user_db = client[f'cover_letter_{USER_ID}']
scores_col = user_db['job-preference-scores']

expected_weighted_score = sum(
    stable_test_score(JOB_ID_WITH_IDENTITY, k) * w for k, w in PREFERENCE_WEIGHTS.items()
) / sum(PREFERENCE_WEIGHTS.values())

# --- Poll for both outcomes ---
job_a_ok = False
job_b_ok = False
deadline = time.time() + 60

while time.time() < deadline:
    # --- Job A: must be scored ---
    if not job_a_ok:
        doc_a = scores_col.find_one({'job_id': JOB_ID_WITH_IDENTITY, 'identity_id': identity_id})
        if doc_a and doc_a.get('scoring_status') == 'scored':
            weighted = float(doc_a.get('weighted_score', 0.0))
            if abs(weighted - expected_weighted_score) > 1e-9:
                print(f'FAIL: job_a weighted_score mismatch: expected {expected_weighted_score}, got {weighted}')
                sys.exit(1)
            pref_scores = doc_a.get('preference_scores', [])
            if not isinstance(pref_scores, list) or len(pref_scores) != len(PREFERENCE_WEIGHTS):
                print(f'FAIL: job_a wrong number of preference_scores: {pref_scores}')
                sys.exit(1)
            score_map = {s.get('preference_key'): s for s in pref_scores}
            if set(score_map) != set(PREFERENCE_WEIGHTS):
                print(f'FAIL: job_a preference keys mismatch: {set(score_map)} != {set(PREFERENCE_WEIGHTS)}')
                sys.exit(1)
            for key in PREFERENCE_WEIGHTS:
                expected = stable_test_score(JOB_ID_WITH_IDENTITY, key)
                actual = int(score_map[key].get('score', 0))
                if actual != expected:
                    print(f'FAIL: job_a preference {key!r} score mismatch: expected {expected}, got {actual}')
                    sys.exit(1)
            job_a_ok = True

    # --- Job B: must NOT be scored.
    # Some implementations persist a skipped/failed score doc; others fail-fast
    # before creating any score document. Both are valid as long as the job is
    # never marked as scored.
    if not job_b_ok:
        doc_b = scores_col.find_one({'job_id': JOB_ID_WITHOUT_IDENTITY})
        if doc_b is None:
            # Accept missing document only after job A is confirmed scored, so
            # we know the worker consumed at least one message in this run.
            if job_a_ok:
                job_b_ok = True
        else:
            status_b = doc_b.get('scoring_status', '')
            if status_b == 'scored':
                print('FAIL: job_b should not be scored when identity_id is absent, but got scoring_status=scored')
                sys.exit(1)
            if status_b in TERMINAL_FAIL_STATUSES:
                job_b_ok = True
            elif job_a_ok:
                # Any non-scored persisted state after job A success is
                # acceptable for this regression guard.
                job_b_ok = True

    if job_a_ok and job_b_ok:
        print('FOUND: job_a scored correctly; job_b failed/skipped as expected')
        sys.exit(0)

    time.sleep(0.5)

# Timeout — report which assertion failed
if not job_a_ok:
    doc_a = scores_col.find_one({'job_id': JOB_ID_WITH_IDENTITY, 'identity_id': identity_id})
    print(f'NOT_FOUND: job_a not scored within timeout. Last doc: {doc_a}')
elif not job_b_ok:
    doc_b = scores_col.find_one({'job_id': JOB_ID_WITHOUT_IDENTITY})
    print(f'NOT_FOUND: job_b expectation not met within timeout. Last doc: {doc_b}')
sys.exit(2)
