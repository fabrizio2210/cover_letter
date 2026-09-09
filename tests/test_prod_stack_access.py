from pathlib import Path
import re
import shutil
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
STACK_FILE = REPO_ROOT / "docker/prod/stack.yml"
MIGRATION_FILE = REPO_ROOT / "docker/prod/mongo-migration.yml"
CICD_FILE = REPO_ROOT / "CICD.sh"
PORTAINER_LABEL = "io.portainer.accesscontrol.users"
PORTAINER_USER = "coverletter-mcp"
MONGO_DATA_PATH = "/mnt/disk-usb1/docker/coverletter/mongo"


def render_stack(*compose_files):
    compose_files = compose_files or (STACK_FILE,)
    compose_args = []
    for compose_file in compose_files:
        compose_args.extend(("--compose-file", str(compose_file)))
    result = subprocess.run(
        [
            "docker",
            "stack",
            "config",
            *compose_args,
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


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is not installed")
class ProductionMongoReplicaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered_stack = render_stack()
        cls.services = rendered_service_blocks(cls.rendered_stack)
        cls.mongo_members = {
            name for name in cls.services if re.fullmatch(r"mongo\d+", name)
        }

    def test_replica_members_have_local_storage_and_fixed_placement(self):
        self.assertEqual({"mongo5", "mongo6", "mongo7"}, self.mongo_members)
        for member in self.mongo_members:
            with self.subTest(member=member):
                block = self.services[member]
                node = f"raspberrypi{member.removeprefix('mongo')}"
                self.assertIn("- --replSet", block)
                self.assertIn("- coverletter-rs", block)
                self.assertIn(f"source: {MONGO_DATA_PATH}", block)
                self.assertIn("target: /data/db", block)
                self.assertIn(f"- node.hostname == {node}", block)
                self.assertIn('memory: "671088640"', block)
                self.assertIn('memory: "268435456"', block)
                self.assertIn("coverletter_backend: null", block)
                self.assertNotIn("traefik_backends", block)

    def test_initializer_is_a_single_idempotent_job(self):
        block = self.services["mongo-rs-init"]
        self.assertIn("mode: replicated-job", block)
        self.assertIn("replicas: 1", block)
        self.assertIn("status.code !== 94", block)
        self.assertIn('_id: "coverletter-rs"', block)
        configured_members = set(
            re.findall(r'\{_id: \d+, host: "(mongo\d+):27017"\}', block)
        )
        self.assertEqual(self.mongo_members, configured_members)

    def test_production_clients_share_uri_matching_replica_members(self):
        discovered_clients = {}
        for service, block in self.services.items():
            matches = re.findall(
                r"^      (?:MONGO_HOST|ME_CONFIG_MONGODB_URL): (mongodb://\S+)$",
                block,
                re.MULTILINE,
            )
            if matches:
                self.assertEqual(1, len(matches), service)
                discovered_clients[service] = matches[0]

        non_clients = self.mongo_members | {
            "redis_cover_letter",
            "ollama",
            "mongo-rs-init",
            "coverletter-frontend",
        }
        self.assertEqual(set(self.services) - non_clients, set(discovered_clients))
        self.assertEqual(1, len(set(discovered_clients.values())))
        self.assertEqual(1, STACK_FILE.read_text().count("mongodb://"))

        uri = next(iter(discovered_clients.values()))
        uri_match = re.fullmatch(
            r"mongodb://(?P<seeds>[^/]+)/\?"
            r"replicaSet=coverletter-rs&w=majority&retryWrites=true",
            uri,
        )
        self.assertIsNotNone(uri_match)
        seed_members = {
            seed.removesuffix(":27017")
            for seed in uri_match.group("seeds").split(",")
        }
        self.assertEqual(self.mongo_members, seed_members)

    def test_mongo_is_unauthenticated_and_not_nfs_backed(self):
        self.assertNotIn("COVERLETTER_MONGO_PASSWORD", self.rendered_stack)
        self.assertNotIn("COVERLETTER_MONGO_PASSWORD", CICD_FILE.read_text())
        self.assertNotIn("--keyFile", self.rendered_stack)
        self.assertNotIn("192.168.100.1", self.rendered_stack)
        self.assertNotIn("published: 27017", self.rendered_stack)

    def test_migration_stack_keeps_every_non_mongo_service_stopped(self):
        migration_services = rendered_service_blocks(
            render_stack(STACK_FILE, MIGRATION_FILE)
        )
        stopped_services = set(migration_services) - self.mongo_members - {
            "mongo-rs-init"
        }
        for service in stopped_services:
            with self.subTest(service=service):
                self.assertIn("replicas: 0", migration_services[service])

        for service in self.mongo_members | {"mongo-rs-init"}:
            with self.subTest(service=service):
                self.assertNotIn("replicas: 0", migration_services[service])

    def test_networks_isolate_backend_and_pin_traefik_routing(self):
        private_only = set(self.services) - {
            "coverletter-api",
            "coverletter-frontend",
            "mongo-express",
        }
        for service in private_only:
            with self.subTest(service=service):
                self.assertIn(
                    "coverletter_backend: null", self.services[service]
                )
                self.assertNotIn("traefik_backends: null", self.services[service])

        for service in ("coverletter-api", "mongo-express"):
            with self.subTest(service=service):
                block = self.services[service]
                self.assertIn("coverletter_backend: null", block)
                self.assertIn("traefik_backends: null", block)
                self.assertIn(
                    "traefik.docker.network: traefik_backends", block
                )

        frontend = self.services["coverletter-frontend"]
        self.assertNotIn("coverletter_backend: null", frontend)
        self.assertIn("traefik_backends: null", frontend)
        self.assertIn("traefik.docker.network: traefik_backends", frontend)


if __name__ == "__main__":
    unittest.main()
