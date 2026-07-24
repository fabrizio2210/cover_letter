from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CICD = (REPO_ROOT / "CICD.sh").read_text()
PRODUCTION_STACK = (REPO_ROOT / "docker/prod/stack.yml").read_text()
OLLAMA_DOCKERFILE = (REPO_ROOT / "docker/ollama/Dockerfile").read_text()
DEV_STACK = (REPO_ROOT / "docker/lib/stack-dev.yml").read_text()
LOCAL_DEV_SCRIPT = (
    REPO_ROOT / "docker/lib/createLocalDevStack.sh"
).read_text()
DEV_OLLAMA_DOCKERFILE = (
    REPO_ROOT / "docker/ollama/Dockerfile-dev"
).read_text()
OLLAMA_SMOKE_SCRIPT = (
    REPO_ROOT / "scripts/smoke-ollama-image.sh"
).read_text()
OLLAMA_PUBLISH_SCRIPT = (
    REPO_ROOT / "scripts/publish-ollama-model.sh"
).read_text()


class OllamaPackagingTests(unittest.TestCase):
    def _extract_single_required(self, pattern, content, source):
        matches = re.findall(pattern, content, re.MULTILINE)
        self.assertEqual(
            1,
            len(matches),
            f"Expected exactly one model selector in {source}, got {matches}",
        )
        return matches[0]

    def test_cicd_uses_native_architecture_without_platform_override(self):
        self.assertIn('aarch64|arm64)', CICD)
        self.assertIn('arch="arm64"', CICD)
        self.assertNotIn('--platform', CICD)
        self.assertNotIn('armv7hf', CICD)

    def test_model_name_is_synchronized_across_deployment_sources(self):
        selectors = {
            "CICD.sh": self._extract_single_required(
                r'^readonly OLLAMA_MODEL_NAME="([^"]+)"$', CICD, "CICD.sh"
            ),
            "docker/lib/createLocalDevStack.sh": self._extract_single_required(
                r'^readonly OLLAMA_MODEL_NAME="([^"]+)"$',
                LOCAL_DEV_SCRIPT,
                "docker/lib/createLocalDevStack.sh",
            ),
            "docker/prod/stack.yml": self._extract_single_required(
                r"^\s+OLLAMA_MODEL:\s+(\S+)\s*$",
                PRODUCTION_STACK,
                "docker/prod/stack.yml",
            ),
            "docker/lib/stack-dev.yml": self._extract_single_required(
                r"^\s+OLLAMA_MODEL:\s+(\S+)\s*$",
                DEV_STACK,
                "docker/lib/stack-dev.yml",
            ),
            "scripts/smoke-ollama-image.sh": self._extract_single_required(
                r'^readonly DEFAULT_MODEL_NAME="([^"]+)"$',
                OLLAMA_SMOKE_SCRIPT,
                "scripts/smoke-ollama-image.sh",
            ),
            "scripts/publish-ollama-model.sh": self._extract_single_required(
                r'^readonly DEFAULT_MODEL_NAME="([^"]+)"$',
                OLLAMA_PUBLISH_SCRIPT,
                "scripts/publish-ollama-model.sh",
            ),
        }

        self.assertEqual(
            1,
            len(set(selectors.values())),
            f"Model selectors are inconsistent: {selectors}",
        )

        publisher_image = self._extract_single_required(
            r'^readonly DEFAULT_MODEL_IMAGE="([^"]+)"$',
            OLLAMA_PUBLISH_SCRIPT,
            "scripts/publish-ollama-model.sh",
        )
        publisher_model_tag = selectors[
            "scripts/publish-ollama-model.sh"
        ].rpartition(":")[2]
        publisher_image_tag = publisher_image.rpartition(":")[2]
        self.assertTrue(publisher_model_tag, "Publisher model name has no tag")
        self.assertTrue(publisher_image_tag, "Publisher image has no tag")
        self.assertEqual(publisher_model_tag, publisher_image_tag)

    def test_model_artifact_is_pinned_by_registry_digest(self):
        match = re.search(
            r'readonly OLLAMA_MODEL_IMAGE="'
            r'fabrizio2210/coverletter-ollama-model@sha256:([^\"]+)"',
            CICD,
        )
        self.assertIsNotNone(match)
        self.assertRegex(match.group(1), r'^[0-9a-f]{64}$')
        self.assertIn(
            'docker buildx imagetools inspect "$OLLAMA_MODEL_IMAGE"',
            CICD,
        )

    def test_runtime_copies_the_promoted_model_and_bakes_the_auxiliary_model(self):
        self.assertIn('ARG MODEL_IMAGE', OLLAMA_DOCKERFILE)
        self.assertIn('FROM ${MODEL_IMAGE} AS model_store', OLLAMA_DOCKERFILE)
        self.assertIn(
            'COPY --from=model_store /models/ /root/.ollama/models/',
            OLLAMA_DOCKERFILE,
        )
        self.assertNotIn('ollama create', OLLAMA_DOCKERFILE)
        self.assertIn('ARG AUXILIARY_MODEL=qwen2.5:1.5b', OLLAMA_DOCKERFILE)
        self.assertIn('ollama pull "$AUXILIARY_MODEL"', OLLAMA_DOCKERFILE)

    def test_production_uses_the_baked_promoted_model(self):
        self.assertIn(
            'image: ${DOCKER_ORG:-fabrizio2210}/coverletter-ollama:'
            '${DEPLOY_TAG:-arm64}',
            PRODUCTION_STACK,
        )
        self.assertIn(
            'QUERY_EXPANSION_MODEL: qwen2.5:1.5b', PRODUCTION_STACK
        )
        self.assertIn(
            'METADATA_NORMALIZATION_MODEL: qwen2.5:1.5b', PRODUCTION_STACK
        )
        self.assertIn('NORMALIZE_JOB_LOCATION: "true"', PRODUCTION_STACK)
        self.assertIn('EXPLICIT_REMOTE_LOCATION: "true"', PRODUCTION_STACK)
        self.assertIn(
            'NORMALIZE_PREFERENCE_GUIDANCE: "true"', PRODUCTION_STACK
        )
        self.assertIn(
            'PREFERENCE_NORMALIZATION_MODEL: qwen2.5:1.5b',
            PRODUCTION_STACK,
        )
        self.assertIn('EVIDENCE_SCOPE_ROUTING: llm', PRODUCTION_STACK)
        self.assertIn(
            'EVIDENCE_SCOPE_MODEL: qwen2.5:1.5b', PRODUCTION_STACK
        )
        self.assertNotIn('ollama pull', PRODUCTION_STACK)

    def test_local_dev_build_uses_the_pinned_model_artifact(self):
        cicd_model_image = re.search(
            r'readonly OLLAMA_MODEL_IMAGE="([^"]+)"', CICD
        )
        dev_model_image = re.search(
            r'readonly OLLAMA_MODEL_IMAGE="([^"]+)"', LOCAL_DEV_SCRIPT
        )

        self.assertIsNotNone(cicd_model_image)
        self.assertIsNotNone(dev_model_image)
        self.assertEqual(cicd_model_image.group(1), dev_model_image.group(1))
        self.assertIn(
            '--file docker/ollama/Dockerfile-dev', LOCAL_DEV_SCRIPT
        )
        self.assertIn(
            '--build-arg "MODEL_IMAGE=$OLLAMA_MODEL_IMAGE"',
            LOCAL_DEV_SCRIPT,
        )

    def test_local_dev_build_references_existing_dockerfiles(self):
        dockerfiles = re.findall(
            r'(?:--file|-f)\s+(docker/[^\s\\]+)', LOCAL_DEV_SCRIPT
        )

        self.assertTrue(dockerfiles)
        for dockerfile in dockerfiles:
            with self.subTest(dockerfile=dockerfile):
                self.assertTrue((REPO_ROOT / dockerfile).is_file())

        self.assertNotIn('crawler-company-discovery', LOCAL_DEV_SCRIPT)
        self.assertIn('Dockerfile-crawler-ycombinator-dev', LOCAL_DEV_SCRIPT)
        self.assertIn('Dockerfile-crawler-hackernews-dev', LOCAL_DEV_SCRIPT)

    def test_dev_runtime_bakes_the_auxiliary_model(self):
        self.assertIn(
            'FROM ${MODEL_IMAGE} AS model_store', DEV_OLLAMA_DOCKERFILE
        )
        self.assertIn(
            'COPY --from=model_store /models/ /root/.ollama/models/',
            DEV_OLLAMA_DOCKERFILE,
        )
        self.assertIn('ARG AUXILIARY_MODEL=qwen2.5:1.5b', DEV_OLLAMA_DOCKERFILE)
        self.assertIn('ollama pull "$AUXILIARY_MODEL"', DEV_OLLAMA_DOCKERFILE)
        self.assertIn('ENTRYPOINT ["/bin/ollama"]', DEV_OLLAMA_DOCKERFILE)
        self.assertIn('CMD ["serve"]', DEV_OLLAMA_DOCKERFILE)

    def test_dev_stack_uses_the_prepared_runtime_without_mutating_it(self):
        self.assertIn(
            'image: fabrizio2210/coverletter-ollama-dev', DEV_STACK
        )
        self.assertIn('QUERY_EXPANSION_MODEL: qwen2.5:1.5b', DEV_STACK)
        self.assertIn('METADATA_NORMALIZATION_MODEL: qwen2.5:1.5b', DEV_STACK)
        self.assertNotIn('ollama pull', DEV_STACK)
        self.assertNotIn('ollama_data:/root/.ollama', DEV_STACK)


if __name__ == "__main__":
    unittest.main()
