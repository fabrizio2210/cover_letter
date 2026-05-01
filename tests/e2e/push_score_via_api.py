import json
import time
import urllib.error
import urllib.request

API_HOST = 'http://api:8080'
LOGIN_PATH = '/api/login'
JOB_ID = '0000000000000000000000dd'
USER_PASSWORD = 'testpassword'

req = urllib.request.Request(API_HOST + LOGIN_PATH, method='POST')
req.add_header('Content-Type', 'application/json')

token = None
login_deadline = time.time() + 30
while time.time() < login_deadline:
    try:
        with urllib.request.urlopen(req, data=json.dumps({'password': USER_PASSWORD, 'username': 'e2e-test-user'}).encode('utf-8'), timeout=5) as resp:
            body = resp.read()
            parsed = json.loads(body)
            token = parsed.get('token')
            if token:
                break
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'Login failed: {exc.code} {exc.reason}')
    except urllib.error.URLError:
        time.sleep(0.5)

if not token:
    raise SystemExit('Login failed: API not reachable')

score_path = f'/api/job-descriptions/{JOB_ID}/score'
score_req = urllib.request.Request(API_HOST + score_path, method='POST')
score_req.add_header('Authorization', f'Bearer {token}')

try:
    with urllib.request.urlopen(score_req, data=b'', timeout=5) as resp:
        body = resp.read()
        parsed = json.loads(body or b'{}')
        if resp.status != 200:
            raise SystemExit(f"Score failed: {resp.status} {body.decode('utf-8', errors='replace')}")
        if parsed.get('message') != 'Scoring queued successfully':
            raise SystemExit(f'Unexpected response body: {parsed}')
except urllib.error.HTTPError as exc:
    body = exc.read()
    raise SystemExit(f"Score failed: {exc.code} {exc.reason} {body.decode('utf-8', errors='replace')}")

print('PUSHED')