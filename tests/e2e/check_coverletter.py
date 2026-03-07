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

found = False
end = time.time() + 30
while time.time() < end:
    res = col.find_one({})
    if res:
        print('FOUND')
        sys.exit(0)
    time.sleep(0.5)

print('NOT_FOUND')
sys.exit(2)
