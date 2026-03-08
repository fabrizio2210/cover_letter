from pymongo import MongoClient
from bson.objectid import ObjectId
import time

mongo_uri = 'mongodb://mongo:27017/'

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

db = client['cover_letter']

# Clean old data
for c in ['fields', 'companies', 'identities', 'recipients', 'cover-letters']:
    try:
        db.drop_collection(c)
    except Exception:
        pass

field = {'field': 'Engineering'}
field_id = db['fields'].insert_one(field).inserted_id

company = {'name': 'TestCorp', 'description': 'Testing company', 'field': field_id}
company_id = db['companies'].insert_one(company).inserted_id

identity = {'identity': 'id-1', 'field': field_id, 'name': 'Test Identity', 'description': 'An identity', 'html_signature': '<p>sig</p>'}
identity_id = db['identities'].insert_one(identity).inserted_id

recipient = {'_id': ObjectId('0000000000000000000000aa'), 'email': 'to@example.test', 'description': 'Recipient for tests', 'name': 'Recipient', 'company': company_id}
recipient_id = db['recipients'].insert_one(recipient).inserted_id


recipient_for_refine = {'_id': ObjectId('0000000000000000000000bb'), 'email': 'to2@example.test', 'description': 'Recipient for refine tests', 'name': 'Recipient', 'company': company_id}
recipient_for_refine_id = db['recipients'].insert_one(recipient_for_refine).inserted_id
cover_letter_for_refine = {
    '_id': ObjectId('0000000000000000000000cc'),
    'recipient_id': recipient_for_refine_id,
    'version': 1,
    'content': 'This is a cover letter to be refined',
    'refined_content': '',
    'created_at': '2024-01-01T12:00:00Z',
    'model_used': 'model-1'
}
db['cover-letters'].insert_one(cover_letter_for_refine)

print('SEEDED', str(recipient_id), str(recipient_for_refine_id))
