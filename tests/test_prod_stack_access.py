from pathlib import Path
import re
import shutil
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
STACK_FILE = REPO_ROOT / "docker/prod/stack.yml"
PORTAINER_LABEL = "io.portainer.accesscontrol.users"
PORTAINER_USER = "coverletter-mcp"


def render_stack():
    result = subprocess.run(
        [
            "docker",
            "stack",
            "config",
            "--compose-file",
            str(STACK_FILE),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Production stack failed to render:\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def rendered_service_blocks(rendered_stack):
    services_match = re.search(
        r"^services:\n(?P<body>.*?)(?=^[^\s])",
        rendered_stack,
        flags=re.MULTILINE | re.DOTALL,
    )
    if services_match is None:
        raise AssertionError("Rendered stack has no services section")

    services_body = services_match.group("body")
    service_headers = list(
        re.finditer(
            r"^  (?P<name>[^\s:\n]+):\n", services_body, re.MULTILINE
        )
    )
    if not service_headers:
        raise AssertionError("Rendered stack contains no services")

    service_blocks = {}
    for index, header in enumerate(service_headers):
        next_offset = (
            service_headers[index + 1].start()
            if index + 1 < len(service_headers)
            else len(services_body)
        )
        service_blocks[header.group("name")] = services_body[
            header.end():next_offset
        ]

    return service_blocks


class ProductionStackAccessTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is not installed")
    def test_every_service_is_visible_to_portainer_observer(self):
        service_blocks = rendered_service_blocks(render_stack())
        expected_label = re.compile(
            rf"^        {re.escape(PORTAINER_LABEL)}: "
            rf"{re.escape(PORTAINER_USER)}$",
            re.MULTILINE,
        )

        missing_services = sorted(
            service_name
            for service_name, service_block in service_blocks.items()
            if expected_label.search(service_block) is None
        )

        self.assertEqual(
            [],
            missing_services,
            "Production services missing Portainer observer access",
        )

    def test_portainer_observer_username_has_one_source_of_truth(self):
        self.assertEqual(1, STACK_FILE.read_text().count(PORTAINER_USER))


if __name__ == "__main__":
    unittest.main()
