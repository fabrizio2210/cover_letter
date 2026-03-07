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

print('SEEDED', str(recipient_id))
