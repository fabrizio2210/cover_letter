import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES_DIR = REPO_ROOT / "docker/x86_64"
COMPOSE_RUNTIME_FILES = (
    REPO_ROOT / "docker/lib/stack-dev.yml",
    REPO_ROOT / "docker/prod/stack.yml",
    REPO_ROOT / "tests/e2e/docker-compose.test.yml",
    REPO_ROOT / "tests/e2e/docker-compose.candidate.yml",
)
DIRECT_SCRIPT_COMMAND = re.compile(
    r"\bpython(?:3)?(?:\s+-u)?\s+(?!-m(?:\s|$))\S+\.py\b"
)


def discover_python_docker_entrypoints():
    """Return every Python CMD declared by the repository's Dockerfiles."""
    entrypoints = []

    for dockerfile in sorted(DOCKERFILES_DIR.glob("Dockerfile*")):
        for line_number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
            if not line.startswith("CMD "):
                continue

            command = json.loads(line.removeprefix("CMD "))
            python_index = next(
                (
                    index
                    for index, argument in enumerate(command)
                    if Path(argument).name in {"python", "python3"}
                ),
                None,
            )
            if python_index is None:
                continue

            arguments = command[python_index + 1 :]
            if len(arguments) < 2 or arguments[0] != "-m":
                raise AssertionError(
                    f"Python CMD must use package mode in "
                    f"{dockerfile.name}:{line_number}: {command}"
                )

            entrypoints.append((dockerfile.name, arguments[1]))

    return entrypoints


class PythonEntrypointTests(unittest.TestCase):
    def test_compose_python_entrypoints_use_package_mode(self):
        for runtime_file in COMPOSE_RUNTIME_FILES:
            with self.subTest(runtime_file=runtime_file.relative_to(REPO_ROOT)):
                direct_commands = DIRECT_SCRIPT_COMMAND.findall(runtime_file.read_text())
                self.assertEqual(
                    direct_commands,
                    [],
                    f"Python entrypoints must use 'python -m' in {runtime_file}",
                )

    def test_all_python_docker_entrypoints_import(self):
        """Keep every Python Docker CMD covered by test-fast."""
        entrypoints = discover_python_docker_entrypoints()
        self.assertTrue(entrypoints, "No Python Docker entrypoints were discovered")

        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)

        # Production and development Dockerfiles share entrypoints. Import each
        # unique target once while retaining all declaring files in diagnostics.
        declarations = {}
        for dockerfile, target in entrypoints:
            declarations.setdefault(target, []).append(dockerfile)

        for target, dockerfiles in declarations.items():
            with self.subTest(dockerfiles=dockerfiles, target=target):
                statement = f"importlib.import_module({target!r})"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        f"import importlib, sys; {statement}",
                    ],
                    cwd=REPO_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(
                    result.returncode,
                    0,
                    f"Python entrypoint declared by {', '.join(dockerfiles)} "
                    f"failed to import:\n{result.stdout}{result.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
