import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES_DIR = REPO_ROOT / "docker/x86_64"


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
            if len(arguments) >= 2 and arguments[0] == "-m":
                mode, target = "module", arguments[1]
            elif arguments and arguments[0].endswith(".py"):
                mode, target = "script", arguments[0]
            else:
                raise AssertionError(
                    f"Unsupported Python CMD in {dockerfile.name}:{line_number}: {command}"
                )

            entrypoints.append((dockerfile.name, mode, target))

    return entrypoints


def import_command(mode, target):
    if mode == "module":
        return REPO_ROOT, f"importlib.import_module({target!r})"

    script = REPO_ROOT / target
    if not script.is_file():
        raise AssertionError(f"Python Docker entrypoint does not exist: {target}")

    # The Telegram dependency currently installed by the combined local test
    # environment is incompatible with Python 3.12. Stub that external API so
    # this smoke test can still validate the entrypoint's repository imports.
    bootstrap = ""
    if target == "src/python/telegram_bot/telegram_bot.py":
        bootstrap = (
            "from unittest.mock import MagicMock; "
            "sys.modules['telegram'] = MagicMock(); "
            "sys.modules['telegram.ext'] = MagicMock(); "
        )

    return script.parent, f"{bootstrap}importlib.import_module({script.stem!r})"


class PythonEntrypointTests(unittest.TestCase):
    def test_all_python_docker_entrypoints_import(self):
        """Keep every Python Docker CMD covered by test-fast."""
        entrypoints = discover_python_docker_entrypoints()
        self.assertTrue(entrypoints, "No Python Docker entrypoints were discovered")

        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)

        # Production and development Dockerfiles share entrypoints. Import each
        # unique target once while retaining all declaring files in diagnostics.
        declarations = {}
        for dockerfile, mode, target in entrypoints:
            declarations.setdefault((mode, target), []).append(dockerfile)

        for (mode, target), dockerfiles in declarations.items():
            with self.subTest(dockerfiles=dockerfiles, mode=mode, target=target):
                cwd, statement = import_command(mode, target)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        f"import importlib, sys; {statement}",
                    ],
                    cwd=cwd,
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
