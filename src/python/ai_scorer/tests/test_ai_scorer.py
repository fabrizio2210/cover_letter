from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from bson import ObjectId


if "ollama" not in sys.modules:
    fake_ollama = types.ModuleType("ollama")

    class _StubClient:
        def __init__(self, host=None):
            self.host = host

        def chat(self, model, messages, options):
            return {"message": {"content": "3"}}

    setattr(fake_ollama, "Client", _StubClient)
    sys.modules["ollama"] = fake_ollama

from src.python.ai_scorer import ai_scorer as ai_scorer_module
from src.python.ai_scorer import common_pb2
from src.python.ai_scorer.ai_scorer import (
    ScoringRunManager,
    build_mongo_client,
    build_ollama_client,
    build_redis_client,
    build_prompt,
    compute_and_persist_aggregate,
    normalize_description_markdown,
    now_timestamp_dict,
    parse_worker_pool_size,
    parse_object_id,
    score_preference,
    stable_test_score,
    process_scoring_job,
    resolve_scoring_context,
)
from src.python.ai_scorer.evals.profile import PRODUCTION_REFERENCE_MODEL, apply_profile


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _matches(doc, filter_doc):
        if "$or" in filter_doc:
            branches = filter_doc.get("$or", [])
            if not any(FakeCollection._matches(doc, branch) for branch in branches):
                return False

        for key, value in filter_doc.items():
            if key == "$or":
                continue
            if isinstance(value, dict) and "$in" in value:
                if doc.get(key) not in value["$in"]:
                    return False
                continue
            if doc.get(key) != value:
                return False
        return True

    def find_one(self, filter_doc):
        for doc in self.docs:
            if self._matches(doc, filter_doc):
                return doc
        return None

    def find(self, filter_doc=None, projection=None):
        if not filter_doc:
            return list(self.docs)
        return [doc for doc in self.docs if self._matches(doc, filter_doc)]

    def update_one(self, filter_doc, update_doc, upsert=False):
        existing = self.find_one(filter_doc)
        if existing is None:
            if not upsert:
                return
            existing = dict(filter_doc)
            self.docs.append(existing)

        for key, value in update_doc.get("$set", {}).items():
            existing[key] = value

    def count_documents(self, filter_doc):
        return len(self.find(filter_doc))


class FakeRedisClient:
    def __init__(self):
        self.published_messages = []

    def publish(self, channel, payload):
        self.published_messages.append((channel, payload))


class FakeOllamaClient:
    def __init__(self, response):
        self.response = response
        self.last_messages = None
        self.last_options = None
        self.last_think = None

    def chat(self, model, messages, options, think=None):
        self.last_messages = messages
        self.last_options = options
        self.last_think = think
        return self.response


class LocationNormalizingOllamaClient:
    def __init__(self, normalization_error=None):
        self.normalization_error = normalization_error
        self.calls = []

    def chat(self, model, messages, options, format=None):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "options": options,
                "format": format,
            }
        )
        if model == "metadata-model":
            if self.normalization_error is not None:
                raise self.normalization_error
            return {"message": {"content": '{"normalized_location":"remote"}'}}
        return {"message": {"content": "N/A"}}


class ProductionPipelineOllamaClient:
    def __init__(self, evidence_scope="description"):
        self.calls = []
        self.evidence_scope = evidence_scope

    def chat(self, model, messages, options, format=None, think=None):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "options": options,
                "format": format,
                "think": think,
            }
        )
        if format != "json":
            return {"message": {"content": "4"}}

        instruction = messages[0]["content"]
        if instruction.startswith("Normalize one raw job-location"):
            content = '{"normalized_location":"remote"}'
        elif instruction.startswith("Classify only whether"):
            content = '{"needs_rewrite":true}'
        elif instruction.startswith("Rewrite one candidate preference"):
            content = '{"normalized_guidance":"I prefer roles with a lot of coding"}'
        elif instruction.startswith("Classify which source"):
            content = f'{{"evidence_scope":"{self.evidence_scope}"}}'
        elif instruction.startswith("Rewrite a candidate job preference"):
            content = '{"search_query":"software implementation coding duties"}'
        else:
            raise AssertionError(f"Unexpected auxiliary instruction: {instruction}")
        return {"message": {"content": content}}


class FakeRawResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeRawOllamaClient:
    def __init__(self, payload):
        self._client = self
        self.response = FakeRawResponse(payload)
        self.requests = []

    def post(self, path, json):
        self.requests.append({"path": path, "json": json})
        return self.response


class AiScorerUnitTests(unittest.TestCase):
    def test_normalize_description_markdown_converts_common_tags(self):
        html_description = (
            "<h2>Role</h2><p>Build <strong>systems</strong> for users.</p>"
            "<ul><li>Python</li><li>Go</li></ul>"
            '<p>Apply at <a href="https://example.com/jobs/1">this link</a>.</p>'
        )

        normalized = normalize_description_markdown(html_description)

        self.assertIn("## Role", normalized)
        self.assertIn("**systems**", normalized)
        self.assertIn("- Python", normalized)
        self.assertIn("- Go", normalized)
        self.assertIn("[this link](https://example.com/jobs/1)", normalized)
        self.assertNotIn("<p>", normalized)

    def test_normalize_description_markdown_keeps_partial_content_for_malformed_html(self):
        malformed = "<div><p><strong>Backend engineer<p>Microservices<br>Remote"

        normalized = normalize_description_markdown(malformed)

        self.assertIn("**Backend engineer", normalized)
        self.assertIn("Microservices", normalized)
        self.assertIn("Remote", normalized)

    def test_normalize_description_markdown_passthrough_plain_text(self):
        plain = "Build reliable APIs and mentor engineers."
        normalized = normalize_description_markdown(plain)
        self.assertEqual(normalized, plain)

    def test_build_prompt_uses_snippet_context(self):
        _, prompt = build_prompt(
            job={
                "title": "Engineer",
                "description": "<p>Hello <strong>world</strong></p><li>Remote</li>",
                "location": "EU",
            },
            company={},
            identity={},
            preference={"guidance": "Remote first"},
            snippets=["Hello world", "Remote collaboration"],
        )

        self.assertIn("Relevant Context Snippets:", prompt)
        self.assertIn("- Hello world", prompt)
        self.assertIn("- Remote collaboration", prompt)
        self.assertNotIn("Job Description:", prompt)

    def test_parse_object_id_handles_valid_and_invalid_values(self):
        oid = ObjectId()
        self.assertEqual(parse_object_id(oid), oid)
        self.assertEqual(parse_object_id(str(oid)), oid)
        self.assertIsNone(parse_object_id("not-an-object-id"))

    def test_stable_test_score_is_deterministic_and_in_range(self):
        score_a = stable_test_score("job-1", "remote")
        score_b = stable_test_score("job-1", "remote")
        self.assertEqual(score_a, score_b)
        self.assertGreaterEqual(score_a, 0)
        self.assertLessEqual(score_a, 5)

    def test_resolve_scoring_context_success(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[
                {
                    "_id": job_id,
                    "company": company_id,
                    "title": "Platform Engineer",
                    "description": "Work on infra",
                }
            ]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "field": field_id,
                    "name": "Fab",
                    "preferences": [
                        {"key": "remote", "guidance": "Remote", "weight": 2, "enabled": True},
                        {"key": "onsite", "guidance": "Onsite", "weight": 1, "enabled": False},
                    ],
                }
            ]
        )

        context, error = resolve_scoring_context(jobs, companies, identities, str(job_id))

        self.assertIsNone(error)
        self.assertIsNotNone(context)
        if context is None:
            self.fail("Expected scoring context")
        _, _, _, enabled = context
        self.assertIsNotNone(enabled)
        if enabled is None:
            self.fail("Expected enabled preferences")
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["key"], "remote")

    def test_resolve_scoring_context_marks_missing_identity(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()

        jobs = FakeCollection(docs=[{"_id": job_id, "company": company_id}])
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id}])
        identities = FakeCollection(docs=[])

        context, error = resolve_scoring_context(jobs, companies, identities, str(job_id))

        self.assertEqual(error, "identity_not_found")
        self.assertIsNotNone(context)

    def test_score_preference_uses_test_mode(self):
        score_result = score_preference(
            ollama_client=None,
            model_name="unused",
            test_mode=True,
            job_id="507f1f77bcf86cd799439011",
            preference={"key": "remote", "guidance": "Remote", "weight": 1, "enabled": True},
            job_doc={},
            company_doc={},
            identity_doc={},
        )

        self.assertTrue(score_result.get("score_available"))
        self.assertGreaterEqual(score_result.get("score", 0), 0)
        self.assertLessEqual(score_result.get("score", 0), 5)

    def test_score_preference_accepts_zero_as_available_score(self):
        client = FakeOllamaClient(
            {
                "message": {
                    "content": "0"
                }
            }
        )

        score_result = score_preference(
            ollama_client=client,
            model_name="qwen2.5:1.5b",
            test_mode=False,
            job_id="507f1f77bcf86cd799439011",
            preference={"key": "remote", "guidance": "Remote", "weight": 1, "enabled": True},
            job_doc={"title": "Engineer", "description": "desc", "location": "EU", "platform": "ashby"},
            company_doc={"name": "Acme", "description": "Infra"},
            identity_doc={"name": "Fab", "description": "Platform"},
        )

        self.assertTrue(score_result.get("score_available"))
        self.assertEqual(score_result.get("score"), 0)

    def test_score_preference_parses_ollama_response(self):
        client = FakeOllamaClient(
            {
                "message": {
                    "content": "4"
                }
            }
        )

        score_result = score_preference(
            ollama_client=client,
            model_name="qwen2.5:1.5b",
            test_mode=False,
            job_id="507f1f77bcf86cd799439011",
            preference={"key": "remote", "guidance": "Remote", "weight": 1, "enabled": True},
            job_doc={"title": "Engineer", "description": "desc", "location": "EU", "platform": "ashby"},
            company_doc={"name": "Acme", "description": "Infra"},
            identity_doc={"name": "Fab", "description": "Platform"},
        )

        self.assertTrue(score_result.get("score_available"))
        self.assertEqual(score_result.get("score"), 4)
        self.assertIsNotNone(client.last_messages)
        if client.last_messages is None:
            self.fail("Expected Ollama messages to be captured")
        system_text = client.last_messages[0]["content"]
        prompt_text = client.last_messages[1]["content"]
        self.assertIn("Preference Guidance:", prompt_text)
        self.assertIn("Job Title:", prompt_text)
        self.assertIn("Job Location:", prompt_text)
        self.assertIn("Relevant Context Snippets:", prompt_text)
        self.assertNotIn("Job Description:", prompt_text)
        self.assertNotIn("Source Platform:", prompt_text)
        self.assertNotIn("Company Name:", prompt_text)
        self.assertNotIn("Company Description:", prompt_text)
        self.assertNotIn("Candidate Identity Name:", prompt_text)
        self.assertNotIn("Candidate Identity Description:", prompt_text)
        self.assertNotIn("Preference Key:", prompt_text)
        self.assertNotIn("Preference Weight:", prompt_text)
        self.assertIn("or N/A", system_text)

    def test_score_preference_passes_configured_generation_controls(self):
        client = FakeOllamaClient({"message": {"content": "4"}})

        with patch.dict(
            os.environ,
            {"SCORING_NUM_PREDICT": "8", "SCORING_THINK": "false"},
            clear=True,
        ):
            score_result = score_preference(
                ollama_client=client,
                model_name="qwen3.5:2b-q4_K_M",
                test_mode=False,
                job_id="507f1f77bcf86cd799439011",
                preference={
                    "key": "remote",
                    "guidance": "Remote",
                    "weight": 1,
                    "enabled": True,
                },
                job_doc={
                    "title": "Engineer",
                    "description": "desc",
                    "location": "EU",
                    "platform": "ashby",
                },
                company_doc={"name": "Acme", "description": "Infra"},
                identity_doc={"name": "Fab", "description": "Platform"},
            )

        self.assertEqual(score_result.get("score"), 4)
        self.assertEqual(client.last_options, {"temperature": 0, "num_predict": 8})
        self.assertIs(client.last_think, False)

    def test_production_profile_exercises_complete_scoring_pipeline(self):
        client = ProductionPipelineOllamaClient()

        with (
            patch.dict(os.environ, {"AUXILIARY_THINK": "false"}, clear=True),
            patch.object(
                ai_scorer_module,
                "retrieve_relevant_snippets",
                return_value=["Build and maintain backend services."],
            ),
            patch.object(
                ai_scorer_module,
                "rerank_scoring_snippets",
                return_value=["Build and maintain backend services."],
            ),
        ):
            apply_profile("production")
            ai_scorer_module._LOCATION_NORMALIZATION_CACHE.clear()
            ai_scorer_module._PREFERENCE_FRAGMENT_CACHE.clear()
            ai_scorer_module._PREFERENCE_NORMALIZATION_CACHE.clear()
            ai_scorer_module._PREFERENCE_EVIDENCE_SCOPE_CACHE.clear()
            ai_scorer_module._QUERY_EXPANSION_CACHE.clear()

            score_result = score_preference(
                ollama_client=client,
                model_name="scorer-model",
                test_mode=False,
                job_id="job-1",
                preference={
                    "key": "coding",
                    "guidance": "It requires a lot of coding",
                    "weight": 1,
                    "enabled": True,
                },
                job_doc={
                    "title": "Backend Engineer",
                    "description": "Build and maintain backend services.",
                    "location": "Remote - EU",
                },
                company_doc={},
                identity_doc={},
            )

        self.assertEqual(score_result.get("score"), 4)
        self.assertEqual(
            [call["model"] for call in client.calls],
            [
                "qwen2.5:1.5b",
                "scorer-model",
                "qwen2.5:1.5b",
                "qwen2.5:1.5b",
                "qwen2.5:1.5b",
                "qwen2.5:1.5b",
                "scorer-model",
            ],
        )
        auxiliary_calls = [call for call in client.calls if call["format"] == "json"]
        self.assertTrue(auxiliary_calls)
        self.assertTrue(all(call["think"] is False for call in auxiliary_calls))
        final_prompt = client.calls[-1]["messages"][-1]["content"]
        self.assertIn("Preference Guidance: I prefer roles with a lot of coding", final_prompt)
        self.assertIn("Job Location: fully remote", final_prompt)

    def test_production_profile_pins_pointwise_routing_to_promoted_model(self):
        client = ProductionPipelineOllamaClient(
            evidence_scope="location_metadata",
        )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                ai_scorer_module,
                "retrieve_relevant_snippets",
                return_value=["The role is fully remote."],
            ),
            patch.object(
                ai_scorer_module,
                "rerank_scoring_snippets",
                return_value=["The role is fully remote."],
            ),
        ):
            apply_profile("production")
            ai_scorer_module._LOCATION_NORMALIZATION_CACHE.clear()
            ai_scorer_module._PREFERENCE_FRAGMENT_CACHE.clear()
            ai_scorer_module._PREFERENCE_NORMALIZATION_CACHE.clear()
            ai_scorer_module._PREFERENCE_EVIDENCE_SCOPE_CACHE.clear()
            ai_scorer_module._QUERY_EXPANSION_CACHE.clear()

            score_preference(
                ollama_client=client,
                model_name="candidate-model",
                test_mode=False,
                job_id="job-1",
                preference={
                    "key": "remote",
                    "guidance": "Prefers fully remote work",
                    "weight": 1,
                    "enabled": True,
                },
                job_doc={
                    "title": "Backend Engineer",
                    "description": "The role is fully remote.",
                    "location": "Remote",
                },
                company_doc={},
                identity_doc={},
            )

        scoring_models = [
            call["model"] for call in client.calls if call["format"] is None
        ]
        self.assertEqual(scoring_models[0], "candidate-model")
        self.assertEqual(scoring_models[-1], "candidate-model")
        self.assertIn(
            PRODUCTION_REFERENCE_MODEL,
            scoring_models[1:-1],
        )
        self.assertNotIn("candidate-model", scoring_models[1:-1])

    def test_score_preference_parses_na_response(self):
        client = FakeOllamaClient(
            {
                "message": {
                    "content": "N/A"
                }
            }
        )

        score_result = score_preference(
            ollama_client=client,
            model_name="qwen2.5:1.5b",
            test_mode=False,
            job_id="507f1f77bcf86cd799439011",
            preference={"key": "remote", "guidance": "Remote", "weight": 1, "enabled": True},
            job_doc={"title": "Engineer", "description": "desc", "location": "EU", "platform": "ashby"},
            company_doc={"name": "Acme", "description": "Infra"},
            identity_doc={"name": "Fab", "description": "Platform"},
        )

        self.assertFalse(score_result.get("score_available"))
        self.assertEqual(score_result.get("score"), 0)

    def test_score_preference_normalizes_dict_and_protobuf_locations_equally(self):
        environment = {
            "NORMALIZE_JOB_LOCATION": "true",
            "EXPLICIT_REMOTE_LOCATION": "true",
            "METADATA_NORMALIZATION_MODEL": "metadata-model",
        }
        jobs = {
            "dict": {
                "title": "Platform Engineer",
                "description": "",
                "location": "Remote - EU",
            },
            "protobuf": common_pb2.Job(
                title="Platform Engineer",
                description="",
                location="Remote - EU",
            ),
        }
        scoring_prompts = {}

        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                ai_scorer_module,
                "retrieve_relevant_snippets",
                return_value=[],
            ),
        ):
            for representation, job in jobs.items():
                with self.subTest(representation=representation):
                    ai_scorer_module._LOCATION_NORMALIZATION_CACHE.clear()
                    client = LocationNormalizingOllamaClient()

                    score_result = score_preference(
                        ollama_client=client,
                        model_name="scorer-model",
                        test_mode=False,
                        job_id="job-1",
                        preference={
                            "key": "remote",
                            "guidance": "I prefer remote work",
                            "weight": 1,
                            "enabled": True,
                        },
                        job_doc=job,
                        company_doc={},
                        identity_doc={},
                    )

                    self.assertFalse(score_result.get("score_available"))
                    self.assertEqual(
                        [call["model"] for call in client.calls],
                        ["metadata-model", "scorer-model"],
                    )
                    scoring_prompt = client.calls[-1]["messages"][-1]["content"]
                    self.assertIn("Job Location: fully remote", scoring_prompt)
                    self.assertEqual(
                        ai_scorer_module.get_field(job, "location"),
                        "Remote - EU",
                    )
                    scoring_prompts[representation] = scoring_prompt

        ai_scorer_module._LOCATION_NORMALIZATION_CACHE.clear()
        self.assertEqual(scoring_prompts["dict"], scoring_prompts["protobuf"])

    def test_protobuf_location_normalization_failure_uses_original_location(self):
        job = common_pb2.Job(
            title="Platform Engineer",
            description="",
            location="Remote - EU",
        )
        client = LocationNormalizingOllamaClient(
            normalization_error=RuntimeError("normalizer unavailable")
        )
        environment = {
            "NORMALIZE_JOB_LOCATION": "true",
            "EXPLICIT_REMOTE_LOCATION": "true",
            "METADATA_NORMALIZATION_MODEL": "metadata-model",
        }

        ai_scorer_module._LOCATION_NORMALIZATION_CACHE.clear()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                ai_scorer_module,
                "retrieve_relevant_snippets",
                return_value=[],
            ),
        ):
            score_result = score_preference(
                ollama_client=client,
                model_name="scorer-model",
                test_mode=False,
                job_id="job-1",
                preference={
                    "key": "remote",
                    "guidance": "I prefer remote work",
                    "weight": 1,
                    "enabled": True,
                },
                job_doc=job,
                company_doc={},
                identity_doc={},
            )

        self.assertFalse(score_result.get("score_available"))
        self.assertEqual(
            [call["model"] for call in client.calls],
            ["metadata-model", "scorer-model"],
        )
        scoring_prompt = client.calls[-1]["messages"][-1]["content"]
        self.assertIn("Job Location: Remote - EU", scoring_prompt)
        self.assertEqual(job.location, "Remote - EU")

    def test_compute_and_persist_aggregate_updates_score_document(self):
        job_id = ObjectId()
        identity_id = ObjectId()

        scores = FakeCollection(
            docs=[
                {
                    "job_id": str(job_id),
                    "identity_id": str(identity_id),
                    "preference_scores": [
                        {
                            "preference_key": "remote",
                            "preference_weight": 2.0,
                            "score": 5,
                            "score_available": True,
                        },
                        {
                            "preference_key": "coding",
                            "preference_weight": 1.0,
                            "score": 3,
                            "score_available": True,
                        },
                    ],
                },
            ]
        )

        compute_and_persist_aggregate(scores, {"_id": job_id}, {"_id": identity_id})

        updated = scores.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(updated)
        if updated is None:
            self.fail("Expected updated score document")
        self.assertEqual(updated.get("scoring_status"), "scored")
        self.assertTrue(updated.get("weighted_score_available"))
        self.assertAlmostEqual(updated.get("weighted_score"), (5 * 2.0 + 3 * 1.0) / (2.0 + 1.0))

    def test_compute_and_persist_aggregate_skips_unavailable_scores(self):
        job_id = ObjectId()
        identity_id = ObjectId()

        scores = FakeCollection(
            docs=[
                {
                    "job_id": str(job_id),
                    "identity_id": str(identity_id),
                    "preference_scores": [
                        {
                            "preference_key": "remote",
                            "preference_weight": 2.0,
                            "score": 5,
                            "score_available": True,
                        },
                        {
                            "preference_key": "culture",
                            "preference_weight": 10.0,
                            "score": 0,
                            "score_available": False,
                        },
                    ],
                },
            ]
        )

        compute_and_persist_aggregate(scores, {"_id": job_id}, {"_id": identity_id})

        updated = scores.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(updated)
        if updated is None:
            self.fail("Expected updated score document")
        self.assertTrue(updated.get("weighted_score_available"))
        self.assertEqual(updated.get("weighted_score"), 5.0)

    def test_compute_and_persist_aggregate_all_scores_unavailable(self):
        job_id = ObjectId()
        identity_id = ObjectId()

        scores = FakeCollection(
            docs=[
                {
                    "job_id": str(job_id),
                    "identity_id": str(identity_id),
                    "preference_scores": [
                        {
                            "preference_key": "remote",
                            "preference_weight": 2.0,
                            "score": 0,
                            "score_available": False,
                        },
                    ],
                },
            ]
        )

        compute_and_persist_aggregate(scores, {"_id": job_id}, {"_id": identity_id})

        updated = scores.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(updated)
        if updated is None:
            self.fail("Expected updated score document")
        self.assertFalse(updated.get("weighted_score_available"))
        self.assertEqual(updated.get("weighted_score"), 0.0)

    def test_process_scoring_job_success_path(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[
                {
                    "_id": job_id,
                    "company": company_id,
                    "title": "Platform Engineer",
                    "description": "distributed systems",
                    "location": "Remote",
                    "platform": "lever",
                }
            ]
        )
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "field": field_id,
                    "name": "Acme",
                    "description": "Infrastructure company",
                }
            ]
        )
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "field": field_id,
                    "name": "Fab",
                    "description": "Platform profile",
                    "preferences": [
                        {"key": "remote", "guidance": "Remote", "weight": 2, "enabled": True},
                        {"key": "backend", "guidance": "Backend", "weight": 1, "enabled": True},
                    ],
                }
            ]
        )
        score_docs = FakeCollection()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="scoring_progress_channel",
            scoring_run_manager=scoring_run_manager,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
        )

        stored_score = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored_score)
        if stored_score is None:
            self.fail("Expected identity score document")
        self.assertEqual(stored_score.get("scoring_status"), "scored")
        preference_scores = stored_score.get("preference_scores")
        self.assertIsInstance(preference_scores, list)
        if not isinstance(preference_scores, list):
            self.fail("Expected preference_scores list")
        self.assertEqual(len(preference_scores), 2)
        for score in preference_scores:
            self.assertIn("scored_at", score)
            self.assertTrue(score.get("score_available", False))

    def test_process_scoring_job_normalizes_protobuf_location(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()
        job_doc = {
            "_id": job_id,
            "company": company_id,
            "title": "Platform Engineer",
            "description": "",
            "location": "Remote - EU",
            "platform": "lever",
        }
        jobs = FakeCollection(docs=[job_doc])
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "field": field_id,
                    "name": "Acme",
                    "description": "Infrastructure company",
                }
            ]
        )
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "field": field_id,
                    "name": "Fab",
                    "description": "Platform profile",
                    "preferences": [
                        {
                            "key": "remote",
                            "guidance": "I prefer remote work",
                            "weight": 1,
                            "enabled": True,
                        }
                    ],
                }
            ]
        )
        score_docs = FakeCollection()
        client = LocationNormalizingOllamaClient()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)
        environment = {
            "NORMALIZE_JOB_LOCATION": "true",
            "EXPLICIT_REMOTE_LOCATION": "true",
            "METADATA_NORMALIZATION_MODEL": "metadata-model",
        }

        ai_scorer_module._LOCATION_NORMALIZATION_CACHE.clear()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                ai_scorer_module,
                "retrieve_relevant_snippets",
                return_value=[],
            ),
        ):
            process_scoring_job(
                job_id=str(job_id),
                job_descriptions_col=jobs,
                companies_col=companies,
                identities_col=identities,
                job_preference_scores_col=score_docs,
                redis_client=FakeRedisClient(),
                scoring_progress_channel="scoring_progress_channel",
                scoring_run_manager=scoring_run_manager,
                ollama_client=client,
                model_name="scorer-model",
                test_mode=False,
                identity_id=str(identity_id),
            )

        self.assertEqual(
            [call["model"] for call in client.calls],
            ["metadata-model", "scorer-model"],
        )
        scoring_prompt = client.calls[-1]["messages"][-1]["content"]
        self.assertIn("Job Location: fully remote", scoring_prompt)
        self.assertEqual(job_doc["location"], "Remote - EU")

    def test_process_scoring_job_rescore_updates_single_doc(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[
                {
                    "_id": job_id,
                    "company": company_id,
                    "title": "Platform Engineer",
                    "description": "distributed systems",
                    "location": "Remote",
                    "platform": "lever",
                }
            ]
        )
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "field": field_id,
                    "name": "Acme",
                    "description": "Infrastructure company",
                }
            ]
        )
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "field": field_id,
                    "name": "Fab",
                    "description": "Platform profile",
                    "preferences": [
                        {"key": "remote", "guidance": "Remote", "weight": 2, "enabled": True},
                        {"key": "backend", "guidance": "Backend", "weight": 1, "enabled": True},
                    ],
                }
            ]
        )
        score_docs = FakeCollection()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)
        redis_client = FakeRedisClient()

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=redis_client,
            scoring_progress_channel="scoring_progress_channel",
            scoring_run_manager=scoring_run_manager,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
        )
        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=redis_client,
            scoring_progress_channel="scoring_progress_channel",
            scoring_run_manager=scoring_run_manager,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
        )

        docs = score_docs.find({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertEqual(len(docs), 1)
        preference_scores = docs[0].get("preference_scores", [])
        self.assertEqual(len(preference_scores), 2)

    def test_process_scoring_job_sets_skipped_when_no_preferences(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(docs=[{"_id": job_id, "company": company_id}])
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id}])
        identities = FakeCollection(docs=[{"_id": identity_id, "field": field_id, "preferences": []}])
        score_docs = FakeCollection()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="scoring_progress_channel",
            scoring_run_manager=scoring_run_manager,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
        )

        stored_score = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored_score)
        if stored_score is None:
            self.fail("Expected skipped score document")
        self.assertEqual(stored_score.get("scoring_status"), "skipped")
        self.assertEqual(stored_score.get("preference_scores"), [])

    def test_resolve_scoring_context_with_identity_id_bypasses_field_lookup(self):
        """Direct identity_id lookup succeeds even when company has no field."""
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[{"_id": job_id, "company": company_id, "title": "SRE", "description": "Ops"}]
        )
        # Company has no field — field-based inference would fail
        companies = FakeCollection(docs=[{"_id": company_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "name": "Fab",
                    "preferences": [
                        {"key": "remote", "guidance": "Remote", "weight": 2, "enabled": True},
                    ],
                }
            ]
        )

        context, error = resolve_scoring_context(
            jobs, companies, identities, str(job_id), identity_id=str(identity_id)
        )

        self.assertIsNone(error)
        self.assertIsNotNone(context)
        if context is None:
            self.fail("Expected scoring context")
        _, _, identity_doc, enabled = context
        self.assertIsNotNone(identity_doc)
        self.assertIsNotNone(enabled)
        self.assertEqual(len(enabled), 1)

    def test_resolve_scoring_context_invalid_identity_id_fails_without_fallback(self):
        """When identity_id is provided but not found, fail explicitly without field inference."""
        company_id = ObjectId()
        job_id = ObjectId()
        field_id = ObjectId()
        real_identity_id = ObjectId()
        wrong_identity_id = ObjectId()  # does not exist in identities collection

        jobs = FakeCollection(docs=[{"_id": job_id, "company": company_id}])
        # Company has a valid field that would resolve the real identity via field inference
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id}])
        identities = FakeCollection(
            docs=[
                {
                    "_id": real_identity_id,
                    "field": field_id,
                    "preferences": [{"key": "remote", "guidance": "Remote", "weight": 1, "enabled": True}],
                }
            ]
        )

        context, error = resolve_scoring_context(
            jobs, companies, identities, str(job_id), identity_id=str(wrong_identity_id)
        )

        self.assertEqual(error, "identity_not_found")

    def test_process_scoring_job_succeeds_with_identity_id_no_company_field(self):
        """process_scoring_job scores correctly when identity_id is provided and company has no field."""
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[
                {
                    "_id": job_id,
                    "company": company_id,
                    "title": "Platform Engineer",
                    "description": "infra work",
                    "location": "Remote",
                    "platform": "greenhouse",
                }
            ]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "name": "Fab",
                    "preferences": [
                        {"key": "remote", "guidance": "Remote", "weight": 2, "enabled": True},
                    ],
                }
            ]
        )
        score_docs = FakeCollection()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="scoring_progress_channel",
            scoring_run_manager=scoring_run_manager,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
            identity_id=str(identity_id),
        )

        stored_score = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored_score)
        if stored_score is None:
            self.fail("Expected identity score document")
        self.assertEqual(stored_score.get("scoring_status"), "scored")

    def test_process_scoring_job_reuses_score_when_guidance_unchanged(self):
        """Existing per-preference scores are reused when guidance snapshot matches."""
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[{
                "_id": job_id, "company": company_id,
                "title": "Eng", "description": "desc", "location": "Remote", "platform": "lever",
            }]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[{
                "_id": identity_id, "name": "Fab",
                "preferences": [
                    {"key": "remote", "guidance": "Remote work", "weight": 1, "enabled": True},
                    {"key": "backend", "guidance": "Backend only", "weight": 1, "enabled": True},
                ],
            }]
        )
        score_docs = FakeCollection(
            docs=[{
                "job_id": str(job_id), "identity_id": str(identity_id),
                "preference_scores": [
                    {"preference_key": "remote", "preference_guidance": "Remote work",
                     "preference_weight": 1.0, "score": 5, "scored_at": {"seconds": 100, "nanos": 0}},
                    {"preference_key": "backend", "preference_guidance": "Backend only",
                     "preference_weight": 1.0, "score": 4, "scored_at": {"seconds": 200, "nanos": 0}},
                ],
                "scoring_status": "scored",
                "weighted_score": 4.5,
            }]
        )
        ollama_client = CountingOllamaClient()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="ch",
            scoring_run_manager=scoring_run_manager,
            ollama_client=ollama_client,
            model_name="test-model",
            test_mode=False,
            identity_id=str(identity_id),
        )

        self.assertEqual(ollama_client.call_count, 0, "Ollama must not be called when guidance is unchanged")
        stored = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("Expected stored score document")
        pref_map = {p["preference_key"]: p for p in stored.get("preference_scores", [])}
        self.assertEqual(pref_map["remote"]["scored_at"]["seconds"], 100, "scored_at must be preserved for reused entry")
        self.assertEqual(pref_map["backend"]["scored_at"]["seconds"], 200, "scored_at must be preserved for reused entry")

    def test_process_scoring_job_recomputes_only_changed_guidance(self):
        """Only the preference whose guidance changed is recomputed; the other is reused."""
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[{
                "_id": job_id, "company": company_id,
                "title": "Eng", "description": "desc", "location": "Remote", "platform": "lever",
            }]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[{
                "_id": identity_id, "name": "Fab",
                "preferences": [
                    # guidance changed from "Remote work" -> "Fully remote"
                    {"key": "remote", "guidance": "Fully remote", "weight": 1, "enabled": True},
                    # guidance unchanged
                    {"key": "backend", "guidance": "Backend only", "weight": 1, "enabled": True},
                ],
            }]
        )
        score_docs = FakeCollection(
            docs=[{
                "job_id": str(job_id), "identity_id": str(identity_id),
                "preference_scores": [
                    {"preference_key": "remote", "preference_guidance": "Remote work",
                     "preference_weight": 1.0, "score": 5, "scored_at": {"seconds": 100, "nanos": 0}},
                    {"preference_key": "backend", "preference_guidance": "Backend only",
                     "preference_weight": 1.0, "score": 4, "scored_at": {"seconds": 200, "nanos": 0}},
                ],
                "scoring_status": "scored",
                "weighted_score": 4.5,
            }]
        )
        ollama_client = CountingOllamaClient()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="ch",
            scoring_run_manager=scoring_run_manager,
            ollama_client=ollama_client,
            model_name="test-model",
            test_mode=False,
            identity_id=str(identity_id),
        )

        self.assertEqual(ollama_client.call_count, 1, "Ollama must be called exactly once for the changed preference")
        stored = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("Expected stored score document")
        pref_map = {p["preference_key"]: p for p in stored.get("preference_scores", [])}
        self.assertEqual(pref_map["remote"]["preference_guidance"], "Fully remote",
                         "New guidance snapshot must be stored after recompute")
        self.assertNotEqual(pref_map["remote"]["scored_at"]["seconds"], 100,
                            "scored_at must be updated for recomputed entry")
        self.assertEqual(pref_map["backend"]["scored_at"]["seconds"], 200,
                         "scored_at must be preserved for reused entry")

    def test_process_scoring_job_removes_stale_preference_on_rescore(self):
        """Preferences removed from identity are absent from preference_scores after rescore."""
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[{
                "_id": job_id, "company": company_id,
                "title": "Eng", "description": "desc", "location": "Remote", "platform": "lever",
            }]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[{
                "_id": identity_id, "name": "Fab",
                # "system_design" was removed from the identity
                "preferences": [
                    {"key": "remote", "guidance": "Remote", "weight": 2, "enabled": True},
                ],
            }]
        )
        score_docs = FakeCollection(
            docs=[{
                "job_id": str(job_id), "identity_id": str(identity_id),
                "preference_scores": [
                    {"preference_key": "remote", "preference_guidance": "Remote",
                     "preference_weight": 2.0, "score": 4, "scored_at": {"seconds": 100, "nanos": 0}},
                    {"preference_key": "system_design", "preference_guidance": "Good architecture",
                     "preference_weight": 1.0, "score": 3, "scored_at": {"seconds": 100, "nanos": 0}},
                ],
                "scoring_status": "scored",
                "weighted_score": 3.67,
            }]
        )
        ollama_client = CountingOllamaClient()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="ch",
            scoring_run_manager=scoring_run_manager,
            ollama_client=ollama_client,
            model_name="test-model",
            test_mode=False,
            identity_id=str(identity_id),
        )

        stored = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("Expected stored score document")
        pref_keys = [p["preference_key"] for p in stored.get("preference_scores", [])]
        self.assertIn("remote", pref_keys)
        self.assertNotIn("system_design", pref_keys, "Removed preference must be absent after rescore")
        self.assertAlmostEqual(stored.get("weighted_score"), 4.0,
                               msg="weighted_score must reflect only remaining preferences")

    def test_process_scoring_job_recomputes_weighted_score_on_weight_change(self):
        """weighted_score is recomputed using updated preference_weight when weight changes."""
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[{
                "_id": job_id, "company": company_id,
                "title": "Eng", "description": "desc", "location": "Remote", "platform": "lever",
            }]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[{
                "_id": identity_id, "name": "Fab",
                "preferences": [
                    # weight changed from 1.0 -> 3.0; guidance unchanged
                    {"key": "remote", "guidance": "Remote", "weight": 3, "enabled": True},
                    {"key": "backend", "guidance": "Backend only", "weight": 1, "enabled": True},
                ],
            }]
        )
        score_docs = FakeCollection(
            docs=[{
                "job_id": str(job_id), "identity_id": str(identity_id),
                "preference_scores": [
                    {"preference_key": "remote", "preference_guidance": "Remote",
                     "preference_weight": 1.0, "score": 5, "scored_at": {"seconds": 100, "nanos": 0}},
                    {"preference_key": "backend", "preference_guidance": "Backend only",
                     "preference_weight": 1.0, "score": 3, "scored_at": {"seconds": 200, "nanos": 0}},
                ],
                "scoring_status": "scored",
                "weighted_score": 4.0,  # (5*1 + 3*1) / (1+1)
            }]
        )
        ollama_client = CountingOllamaClient()
        scoring_run_manager = ScoringRunManager(jobs, companies, score_docs)

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            redis_client=FakeRedisClient(),
            scoring_progress_channel="ch",
            scoring_run_manager=scoring_run_manager,
            ollama_client=ollama_client,
            model_name="test-model",
            test_mode=False,
            identity_id=str(identity_id),
        )

        self.assertEqual(ollama_client.call_count, 0, "Ollama must not be called when only weight changed")
        stored = score_docs.find_one({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("Expected stored score document")
        # weighted_score = (5*3 + 3*1) / (3+1) = 18/4 = 4.5
        self.assertAlmostEqual(stored.get("weighted_score"), 4.5,
                               msg="weighted_score must be recomputed from updated weights")


class TimestampTests(unittest.TestCase):
    def test_now_timestamp_dict_shape(self):
        ts = now_timestamp_dict()
        self.assertIn("seconds", ts)
        self.assertIn("nanos", ts)
        self.assertIsInstance(ts["seconds"], int)
        self.assertEqual(ts["nanos"], 0)


class CountingOllamaClient:
    """Fake Ollama client that tracks how many times chat() is called."""

    def __init__(self):
        self.call_count = 0

    def chat(self, model, messages, options):
        self.call_count += 1
        return {"message": {"content": "3"}}


class WorkerPoolConfigTests(unittest.TestCase):
    def test_parse_worker_pool_size_valid(self):
        self.assertEqual(parse_worker_pool_size("2"), 2)

    def test_parse_worker_pool_size_invalid_falls_back_to_one(self):
        self.assertEqual(parse_worker_pool_size("not-a-number"), 1)
        self.assertEqual(parse_worker_pool_size("0"), 1)
        self.assertEqual(parse_worker_pool_size("-4"), 1)


class BuildOllamaClientTests(unittest.TestCase):
    def test_build_ollama_client_applies_request_timeout(self):
        with patch.object(ai_scorer_module.ollama, "Client") as mock_client_cls:
            build_ollama_client("http://ollama:11434")

        mock_client_cls.assert_called_once_with(
            host="http://ollama:11434",
            timeout=ai_scorer_module.OLLAMA_REQUEST_TIMEOUT,
        )

    def test_build_ollama_client_without_host_still_applies_timeout(self):
        with patch.object(ai_scorer_module.ollama, "Client") as mock_client_cls:
            build_ollama_client(None)

        mock_client_cls.assert_called_once_with(
            timeout=ai_scorer_module.OLLAMA_REQUEST_TIMEOUT,
        )

    def test_ollama_request_timeout_bounds_read_and_connect(self):
        timeout = ai_scorer_module.OLLAMA_REQUEST_TIMEOUT
        self.assertEqual(timeout.read, 300.0)
        self.assertEqual(timeout.connect, 10.0)


class BuildRedisClientTests(unittest.TestCase):
    def test_build_redis_client_applies_socket_timeouts(self):
        with patch.object(ai_scorer_module.redis, "Redis") as mock_redis_cls:
            build_redis_client("redis-host", 6380)

        mock_redis_cls.assert_called_once_with(
            host="redis-host",
            port=6380,
            socket_connect_timeout=5,
            socket_timeout=60,
            socket_keepalive=True,
            health_check_interval=30,
        )

    def test_redis_timeout_constants_are_bounded(self):
        self.assertEqual(ai_scorer_module.REDIS_CONNECT_TIMEOUT, 5)
        self.assertEqual(ai_scorer_module.REDIS_SOCKET_TIMEOUT, 60)
        self.assertEqual(ai_scorer_module.REDIS_HEALTH_CHECK_INTERVAL, 30)


class BuildMongoClientTests(unittest.TestCase):
    def test_build_mongo_client_applies_timeouts(self):
        with patch.object(ai_scorer_module, "MongoClient") as mock_mongo_cls:
            build_mongo_client("mongodb://mongo:27017/")

        mock_mongo_cls.assert_called_once_with(
            "mongodb://mongo:27017/",
            connectTimeoutMS=10000,
            serverSelectionTimeoutMS=30000,
            socketTimeoutMS=60000,
        )

    def test_mongo_timeout_constants_are_bounded(self):
        self.assertEqual(ai_scorer_module.MONGO_CONNECT_TIMEOUT_MS, 10000)
        self.assertEqual(ai_scorer_module.MONGO_SERVER_SELECTION_TIMEOUT_MS, 30000)
        self.assertEqual(ai_scorer_module.MONGO_SOCKET_TIMEOUT_MS, 60000)


class ScoringOptionsTests(unittest.TestCase):
    def test_resolve_scoring_options_uses_existing_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                ai_scorer_module.resolve_scoring_options(),
                {"temperature": 0},
            )

    def test_resolve_scoring_options_accepts_generation_limit(self):
        environment = {
            "SCORING_TEMPERATURE": "0.25",
            "SCORING_SEED": "7",
            "SCORING_NUM_PREDICT": "8",
        }

        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                ai_scorer_module.resolve_scoring_options(),
                {"temperature": 0.25, "seed": 7, "num_predict": 8},
            )

    def test_resolve_scoring_options_rejects_non_positive_generation_limit(self):
        with patch.dict(
            os.environ,
            {"SCORING_NUM_PREDICT": "0"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "must be greater than zero"):
                ai_scorer_module.resolve_scoring_options()


class ScoringThinkTests(unittest.TestCase):
    def test_resolve_scoring_think_defaults_to_unspecified(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ai_scorer_module.resolve_scoring_think())

    def test_resolve_scoring_think_accepts_boolean_values(self):
        values = (("true", True), ("1", True), ("false", False), ("0", False))
        for value, expected in values:
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"SCORING_THINK": value},
                clear=True,
            ):
                self.assertIs(ai_scorer_module.resolve_scoring_think(), expected)

    def test_resolve_scoring_think_rejects_invalid_value(self):
        with patch.dict(
            os.environ,
            {"SCORING_THINK": "sometimes"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                ai_scorer_module.resolve_scoring_think()


class RawScoringRequestTests(unittest.TestCase):
    def test_alternate_scoring_paths_apply_generation_controls(self):
        payload = {
            "message": {"content": "4"},
            "logprobs": [
                {
                    "logprob": -0.1,
                    "top_logprobs": [{"token": "4", "logprob": -0.1}],
                }
            ],
        }
        scorers = (
            ai_scorer_module.request_preference_score_with_confidence,
            ai_scorer_module.request_preference_score_expectation,
        )

        for scorer in scorers:
            with self.subTest(scorer=scorer.__name__):
                client = FakeRawOllamaClient(payload)
                with patch.dict(
                    os.environ,
                    {"SCORING_NUM_PREDICT": "8", "SCORING_THINK": "false"},
                    clear=True,
                ):
                    scorer(
                        client,
                        "qwen3.5:2b-q4_K_M",
                        {"key": "remote", "guidance": "Remote"},
                        {"title": "Engineer", "description": "desc"},
                        {},
                        {},
                        ["Remote role"],
                    )

                self.assertEqual(len(client.requests), 1)
                request = client.requests[0]
                self.assertEqual(request["path"], "/api/chat")
                self.assertEqual(
                    request["json"]["options"],
                    {"temperature": 0, "num_predict": 8},
                )
                self.assertIs(request["json"]["think"], False)
                self.assertIs(request["json"]["logprobs"], True)
                self.assertEqual(request["json"]["top_logprobs"], 20)

    def test_raw_payload_preserves_legacy_temperature_and_seed_behavior(self):
        with patch.dict(
            os.environ,
            {"SCORING_TEMPERATURE": "0.75", "SCORING_SEED": "7"},
            clear=True,
        ):
            payload = ai_scorer_module.build_raw_scoring_chat_payload(
                "scorer-model",
                [{"role": "user", "content": "score this"}],
            )

        self.assertEqual(payload["options"], {"temperature": 0})
        self.assertNotIn("think", payload)


if __name__ == "__main__":
    unittest.main()
