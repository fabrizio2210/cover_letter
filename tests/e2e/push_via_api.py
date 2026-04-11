import time
import json
import urllib.request
import urllib.error

API_HOST = 'http://api:8080'
LOGIN_PATH = '/api/login'
import threading

RECIPIENT_ID = '0000000000000000000000aa'
RECIPIENT_FOR_REFINE_ID = '0000000000000000000000bb'
COVER_LETTER_ID_FOR_REFINE = '0000000000000000000000cc'
ADMIN_PASSWORD = 'testpassword'

# ... (rest of the login logic remains the same)

# Obtain token
req = urllib.request.Request(API_HOST + LOGIN_PATH, method='POST')
req.add_header('Content-Type', 'application/json')
token = None
login_deadline = time.time() + 30
while time.time() < login_deadline:
    try:
        with urllib.request.urlopen(req, data=json.dumps({'password': ADMIN_PASSWORD}).encode('utf-8'), timeout=5) as resp:
            body = resp.read()
            parsed = json.loads(body)
            token = parsed.get('token')
            if token:
                break
    except urllib.error.HTTPError as e:
        raise SystemExit(f'Login failed: {e.code} {e.reason}')
    except urllib.error.URLError:
        time.sleep(0.5)

if not token:
    raise SystemExit('Login failed: API not reachable')

results = {}

def call_generate(token):
    # Call generate endpoint
    gen_path = f'/api/recipients/{RECIPIENT_ID}/generate-cover-letter'
    req = urllib.request.Request(API_HOST + gen_path, method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, data=b'', timeout=5) as resp:
            if resp.status == 200:
                results['generate'] = 'OK'
            else:
                body = resp.read()
                results['generate'] = f'Generate failed: {resp.status} {body}'
    except urllib.error.HTTPError as e:
        body = e.read()
        results['generate'] = f'Generate failed: {e.code} {e.reason} {body}'

def call_refine(token):
    # Call refine endpoint
    refine_path = f'/api/cover-letters/{COVER_LETTER_ID_FOR_REFINE}/refine'
    req = urllib.request.Request(API_HOST + refine_path, method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/json')
    data = json.dumps({'prompt': 'Make it more professional'}).encode('utf-8')
    try:
        with urllib.request.urlopen(req, data=data, timeout=5) as resp:
            if resp.status == 200:
                results['refine'] = 'OK'
            else:
                body = resp.read()
                results['refine'] = f'Refine failed: {resp.status} {body}'
    except urllib.error.HTTPError as e:
        body = e.read()
        results['refine'] = f'Refine failed: {e.code} {e.reason} {body}'

generate_thread = threading.Thread(target=call_generate, args=(token,))
refine_thread = threading.Thread(target=call_refine, args=(token,))

generate_thread.start()
refine_thread.start()

generate_thread.join()
refine_thread.join()

if results.get('generate') == 'OK' and results.get('refine') == 'OK':
    print('PUSHED')
else:
    raise SystemExit(f"Failed: {results}")
