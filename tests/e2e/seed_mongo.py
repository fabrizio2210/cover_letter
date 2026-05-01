from pymongo import MongoClient
from bson.objectid import ObjectId
import hashlib
import time

mongo_uri = 'mongodb://mongo:27017/'

SCORER_JOB_ID = ObjectId('0000000000000000000000dd')

# Must match the username used in push_via_api.py
ADMIN_USERNAME = 'e2e-test-user'
_h = hashlib.sha256(ADMIN_USERNAME.encode()).digest()
USER_ID = _h[:16].hex()

# Wait until Mongo is available
end = time.time() + 30
client = None
while time.time() < end:
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        client.server_info()
        break
    except Exception:
        time.sleep(0.5)

if not client:
    raise SystemExit('MongoDB not reachable')

# Global collections (job-descriptions, companies, fields) live in DB_NAME
global_db = client['cover_letter']
# Per-user collections (identities, recipients, cover-letters, job-preference-scores)
# live in cover_letter_<userID>, mirroring the Go API's GetDatabaseName logic
user_db = client[f'cover_letter_{USER_ID}']

# Clean old data
for c in ['fields', 'companies', 'job-descriptions', 'jobs']:
    try:
        global_db.drop_collection(c)
    except Exception:
        pass
for c in ['identities', 'recipients', 'cover-letters', 'job-preference-scores']:
    try:
        user_db.drop_collection(c)
    except Exception:
        pass

field = {'field': 'Engineering'}
field_id = global_db['fields'].insert_one(field).inserted_id

company = {'name': 'TestCorp', 'description': 'Testing company', 'field': field_id, 'field_id': field_id}
company_id = global_db['companies'].insert_one(company).inserted_id

identity = {
    'identity': 'id-1',
    'field': field_id,
    'field_id': field_id,
    'name': 'Test Identity',
    'description': 'An identity',
    'html_signature': '<p>sig</p>',
    'preferences': [
        {
            'key': 'remote',
            'guidance': 'Remote',
            'weight': 2.0,
            'enabled': True,
        },
        {
            'key': 'backend',
            'guidance': 'Backend',
            'weight': 1.0,
            'enabled': True,
        },
    ],
}
identity_id = user_db['identities'].insert_one(identity).inserted_id

recipient = {
    '_id': ObjectId('0000000000000000000000aa'),
    'email': 'to@example.test',
    'description': 'Recipient for tests',
    'name': 'Recipient',
    'company': company_id,
    'company_id': company_id,
}
recipient_id = user_db['recipients'].insert_one(recipient).inserted_id


recipient_for_refine = {
    '_id': ObjectId('0000000000000000000000bb'),
    'email': 'to2@example.test',
    'description': 'Recipient for refine tests',
    'name': 'Recipient',
    'company': company_id,
    'company_id': company_id,
}
recipient_for_refine_id = user_db['recipients'].insert_one(recipient_for_refine).inserted_id
cover_letter_for_refine = {
    '_id': ObjectId('0000000000000000000000cc'),
    'recipient_id': str(recipient_for_refine_id),
    'cover_letter': 'This is a cover letter to be refined',
    'conversation_id': 'test-conversation-id',
    'prompt': 'Initial prompt',
    'history': [
        {'role': 'user', 'parts': [{'text': 'Initial prompt'}]},
        {'role': 'model', 'parts': [{'text': 'This is a cover letter to be refined'}]}
    ],
    'created_at': {'seconds': 1704067200, 'nanos': 0},
}
user_db['cover-letters'].insert_one(cover_letter_for_refine)

job_description = {
    '_id': SCORER_JOB_ID,
    'company': company_id,
    'company_id': company_id,
    'title': 'Platform Engineer',
    'description': 'Build backend services and distributed systems for remote teams.',
    'location': 'Remote',
    'platform': 'lever',
    'created_at': {'seconds': 1704067200, 'nanos': 0},
    'updated_at': {'seconds': 1704067200, 'nanos': 0},
}
global_db['job-descriptions'].insert_one(job_description)

print('SEEDED', str(recipient_id), str(recipient_for_refine_id), str(SCORER_JOB_ID))
