from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CICD = (REPO_ROOT / "CICD.sh").read_text()
PRODUCTION_STACK = (REPO_ROOT / "docker/prod/stack.yml").read_text()
OLLAMA_DOCKERFILE = (REPO_ROOT / "docker/ollama/Dockerfile").read_text()


class OllamaPackagingTests(unittest.TestCase):
    def test_cicd_uses_native_architecture_without_platform_override(self):
        self.assertIn('aarch64|arm64)', CICD)
        self.assertIn('arch="arm64"', CICD)
        self.assertNotIn('--platform', CICD)
        self.assertNotIn('armv7hf', CICD)

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

    def test_runtime_copies_the_pre_registered_model_store(self):
        self.assertIn('ARG MODEL_IMAGE', OLLAMA_DOCKERFILE)
        self.assertIn('FROM ${MODEL_IMAGE} AS model_store', OLLAMA_DOCKERFILE)
        self.assertIn(
            'COPY --from=model_store /models/ /root/.ollama/models/',
            OLLAMA_DOCKERFILE,
        )
        self.assertNotIn('ollama create', OLLAMA_DOCKERFILE)
        self.assertNotIn('ollama pull', OLLAMA_DOCKERFILE)

    def test_production_uses_the_baked_promoted_model(self):
        self.assertIn(
            'image: ${DOCKER_ORG:-fabrizio2210}/coverletter-ollama:'
            '${DEPLOY_TAG:-arm64}',
            PRODUCTION_STACK,
        )
        self.assertIn(
            'OLLAMA_MODEL: '
            'ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16',
            PRODUCTION_STACK,
        )
        self.assertNotIn('ollama pull', PRODUCTION_STACK)


if __name__ == "__main__":
    unittest.main()
