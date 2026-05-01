from bson.objectid import ObjectId
from pymongo import MongoClient
import hashlib
import sys
import time

MONGO_URI = 'mongodb://mongo:27017/'
ADMIN_USERNAME = 'e2e-test-user'
_h = hashlib.sha256(ADMIN_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()
JOB_ID = '0000000000000000000000dd'
IDENTITY_NAME = 'Test Identity'
PREFERENCE_WEIGHTS = {
    'remote': 2.0,
    'backend': 1.0,
}


def stable_test_score(job_id, preference_key):
    seed_text = f'{job_id}:{preference_key}'
    seed = sum(ord(ch) for ch in seed_text)
    return (seed % 5) + 1


expected_scores = {key: stable_test_score(JOB_ID, key) for key in PREFERENCE_WEIGHTS}
expected_weighted_score = sum(expected_scores[key] * weight for key, weight in PREFERENCE_WEIGHTS.items()) / sum(PREFERENCE_WEIGHTS.values())
expected_max_score = len(PREFERENCE_WEIGHTS) * 5

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
    print('NOT_FOUND')
    sys.exit(2)

global_db = client['cover_letter']
user_db = client[f'cover_letter_{USER_ID}']
jobs_col = global_db['job-descriptions']
identities_col = user_db['identities']
scores_col = user_db['job-preference-scores']

identity = identities_col.find_one({'name': IDENTITY_NAME})
if not identity:
    print('NOT_FOUND')
    sys.exit(2)

expected_identity_id = str(identity['_id'])
expected_job_object_id = ObjectId(JOB_ID)

end = time.time() + 30
while time.time() < end:
    job = jobs_col.find_one({'_id': expected_job_object_id})
    score_doc = scores_col.find_one({'job_id': JOB_ID, 'identity_id': expected_identity_id})

    if job and score_doc and score_doc.get('scoring_status') == 'scored':
        if abs(float(score_doc.get('weighted_score', 0.0)) - expected_weighted_score) > 1e-9:
            print('NOT_FOUND')
            sys.exit(2)

        if int(score_doc.get('max_score', 0)) != expected_max_score:
            print('NOT_FOUND')
            sys.exit(2)

        preference_scores = score_doc.get('preference_scores', [])
        if not isinstance(preference_scores, list) or len(preference_scores) != len(PREFERENCE_WEIGHTS):
            print('NOT_FOUND')
            sys.exit(2)

        score_map = {doc.get('preference_key'): doc for doc in preference_scores}
        if set(score_map) != set(PREFERENCE_WEIGHTS):
            print('NOT_FOUND')
            sys.exit(2)

        for key, expected_score in expected_scores.items():
            score_doc = score_map[key]
            if int(score_doc.get('score', 0)) != expected_score:
                print('NOT_FOUND')
                sys.exit(2)
            if float(score_doc.get('preference_weight', 0.0)) != PREFERENCE_WEIGHTS[key]:
                print('NOT_FOUND')
                sys.exit(2)
            scored_at = score_doc.get('scored_at', {})
            if not isinstance(scored_at, dict) or 'seconds' not in scored_at or 'nanos' not in scored_at:
                print('NOT_FOUND')
                sys.exit(2)

        print('FOUND')
        sys.exit(0)

    time.sleep(0.5)

print('NOT_FOUND')
sys.exit(2)