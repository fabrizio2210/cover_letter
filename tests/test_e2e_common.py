import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMON_SH = REPO_ROOT / "tests" / "e2e" / "common.sh"


class ComposeServiceDetectionTest(unittest.TestCase):
    def run_bash(self, body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", body],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_match_does_not_fail_when_service_list_exceeds_pipe_buffer(self) -> None:
        result = self.run_bash(
            f"""
set -euo pipefail
COMPOSE_FILE=test-compose.yml
. {COMMON_SH}
docker() {{
  printf 'mongo\\nredis\\n'
  for ((i = 0; i < 10000; i++)); do
    printf 'service-%s\\n' "$i"
  done
}}
e2e_compose_has_service redis
"""
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_service_returns_false(self) -> None:
        result = self.run_bash(
            f"""
set -euo pipefail
COMPOSE_FILE=test-compose.yml
. {COMMON_SH}
docker() {{
  printf 'mongo\\nredis\\napi\\n'
}}
if e2e_compose_has_service dispatcher; then
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
