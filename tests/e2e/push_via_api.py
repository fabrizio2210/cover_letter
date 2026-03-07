import time
import json
import urllib.request
import urllib.error

API_HOST = 'http://api:8080'
LOGIN_PATH = '/api/login'
RECIPIENT_ID = '0000000000000000000000aa'
ADMIN_PASSWORD = 'testpassword'

# Wait until API is available
end = time.time() + 60
while time.time() < end:
    try:
        req = urllib.request.Request(API_HOST + LOGIN_PATH, method='POST')
        data = json.dumps({'password': ADMIN_PASSWORD}).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, data=data, timeout=2) as resp:
            if resp.status == 200:
                break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit('API not reachable')

# Obtain token
req = urllib.request.Request(API_HOST + LOGIN_PATH, method='POST')
req.add_header('Content-Type', 'application/json')
try:
    with urllib.request.urlopen(req, data=json.dumps({'password': ADMIN_PASSWORD}).encode('utf-8'), timeout=5) as resp:
        body = resp.read()
        parsed = json.loads(body)
        token = parsed.get('token')
        if not token:
            raise SystemExit('No token returned')
except urllib.error.HTTPError as e:
    raise SystemExit(f'Login failed: {e.code} {e.reason}')

# Call generate endpoint
gen_path = f'/api/recipients/{RECIPIENT_ID}/generate-cover-letter'
req = urllib.request.Request(API_HOST + gen_path, method='POST')
req.add_header('Authorization', f'Bearer {token}')
try:
    with urllib.request.urlopen(req, data=b'', timeout=5) as resp:
        if resp.status == 200:
            print('PUSHED')
        else:
            body = resp.read()
            raise SystemExit(f'Generate failed: {resp.status} {body}')
except urllib.error.HTTPError as e:
    body = e.read()
    raise SystemExit(f'Generate failed: {e.code} {e.reason} {body}')
