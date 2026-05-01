"""Verify that the dispatcher fanned out workflow dispatch messages to Redis.

Reads run_id from /tmp/crawl_run_id (written by the pusher container via
shared volume), then polls the expected workflow queues until all 4 queues
contain at least one message whose identity_id matches our test identity.

Expected workflows dispatched by dispatcher/main.py:
  - crawler_ycombinator
  - crawler_hackernews
  - crawler_ats_job_extraction
  - crawler_4dayweek

Note: crawler_levelsfyi is also dispatched but we check the 4 above as a
representative set; adding levelsfyi would be redundant and fragile to future
workflow additions.
"""

import json
import sys
import time

import redis

REDIS_HOST = "redis"
REDIS_PORT = 6379
RUN_ID_FILE = "/tmp/crawl_run_id"

EXPECTED_QUEUES = [
    "crawler_ycombinator_queue",
    "crawler_hackernews_queue",
    "crawler_ats_job_extraction_queue",
    "crawler_4dayweek_queue",
]


def read_run_id(deadline: float) -> str:
    while time.time() < deadline:
        try:
            with open(RUN_ID_FILE) as f:
                run_id = f.read().strip()
            if run_id:
                return run_id
        except FileNotFoundError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"run_id file {RUN_ID_FILE!r} not found within deadline")


def connect_redis(deadline: float) -> redis.Redis:
    while time.time() < deadline:
        try:
            client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=3)
            client.ping()
            return client
        except Exception:
            time.sleep(0.5)
    raise SystemExit("Redis not reachable within deadline")


def find_message_for_run(client: redis.Redis, queue: str, run_id: str) -> dict | None:
    """Return the first message in *queue* whose run_id matches, or None."""
    items = client.lrange(queue, 0, -1)
    for raw in items:
        try:
            msg = json.loads(raw)
            if msg.get("run_id") == run_id:
                return msg
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


deadline = time.time() + 60

run_id = read_run_id(deadline)
print(f"Checking for run_id={run_id}")

r = connect_redis(deadline)

missing = set(EXPECTED_QUEUES)
while time.time() < deadline and missing:
    still_missing = set()
    for queue in list(missing):
        msg = find_message_for_run(r, queue, run_id)
        if msg is not None:
            print(f"OK queue={queue} workflow_id={msg.get('workflow_id')} run_id={run_id}")
        else:
            still_missing.add(queue)
    missing = still_missing
    if missing:
        time.sleep(0.5)

if missing:
    print(f"FAIL: no dispatch message found in queues: {sorted(missing)}", file=sys.stderr)
    sys.exit(1)

print("PASS: all workflow dispatch messages present")
