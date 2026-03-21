from pymongo import MongoClient
import sys
import time

mongo_uri = 'mongodb://mongo:27017/'

# Wait for mongo and poll for cover-letter doc
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
    print('NOT_FOUND')
    sys.exit(2)

db = client['cover_letter']
col = db['cover-letters']

found_generate = False
found_refine = False
end = time.time() + 30
while time.time() < end:
    # Check for the generated cover letter. This is a new document, so we expect two documents in the collection
    if col.count_documents({}) == 2:
        found_generate = True

    # Check for the refined cover letter (ai_querier sets updated_at on iteration)
    res_refine = col.find_one({'updated_at': {'$exists': True}})
    if res_refine:
        found_refine = True

    if found_generate and found_refine:
        print('FOUND')
        sys.exit(0)
    time.sleep(0.5)

print('NOT_FOUND')
sys.exit(2)
