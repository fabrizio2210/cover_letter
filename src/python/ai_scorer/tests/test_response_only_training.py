from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.python.ai_scorer.scoring_prompt import SCORING_SYSTEM_INSTRUCTION
from src.python.ai_scorer.training.fine_tune_merge import resolve_adapter_dir
from src.python.ai_scorer.training.fine_tune_package import _ollama_create, _write_modelfile
from src.python.ai_scorer.training.fine_tune_models import (
    CONTRACT_PATH,
    DEFAULT_MODEL_PROFILE,
    MODEL_PROFILES,
    apply_chat_template_ids,
    load_causal_lm,
    resolve_lora_target_modules,
    resolve_model_profile,
    resolve_run_model_profile,
)
from src.python.ai_scorer.training.fine_tune_train import (
    LOSS_MODES,
    _CausalLMCollator,
    _IGNORE_INDEX,
    _configure_cpu_runtime,
    _encode_chat_full,
    _encode_response_only,
    _encode_training_example,
    _resolved_training_configuration,
)
from src.python.ai_scorer.training.fine_tune_runtime import detect_runtime
from src.python.ai_scorer.training.fine_tune_validate import (
    build_validation_summary,
    discover_checkpoints,
    parse_generated_score,
    resolve_run_dataset_dir,
    select_checkpoint,
    verify_run_dataset_hash,
)
from src.python.ai_scorer.training.training_balance import BALANCED_MODE, SAMPLING_MODES
from src.python.ai_scorer.training.cli import _cmd_label, _cmd_train, build_parser


class _FakeQwenTokenizer:
    eos_token_id = 99
    pad_token_id = 0
    padding_side = "right"

    _role_ids = {"system": 10, "user": 11, "assistant": 12}

    @staticmethod
    def _content_ids(content: str) -> list[int]:
        return [100 + ord(character) for character in content]

    def apply_chat_template(
        self,
        messages,
        *,
        tokenize,
        add_generation_prompt,
        enable_thinking,
        return_dict,
    ):
        if not tokenize:
            raise AssertionError("The encoder must request tokenized chat-template output")
        if enable_thinking:
            raise AssertionError("Training must disable Qwen thinking mode")
        if return_dict:
            raise AssertionError("Training must request plain token IDs")
        ids: list[int] = []
        for message in messages:
            ids.extend([self._role_ids[message["role"]]])
            ids.extend(self._content_ids(message["content"]))
            ids.extend([self.eos_token_id, 98])
        if add_generation_prompt:
            ids.append(self._role_ids["assistant"])
        return ids


class _DictReturningTokenizer(_FakeQwenTokenizer):
    def apply_chat_template(self, *args, **kwargs):
        return {"input_ids": super().apply_chat_template(*args, **kwargs)}


def _messages(with_system: bool = True, user_content: str = "job") -> list[dict]:
    messages = []
    if with_system:
        messages.append({"role": "system", "content": "score only"})
    messages.extend(
        [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": "3"},
        ]
    )
    return messages


class TrainingLossModeEncodingTests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = _FakeQwenTokenizer()

    def test_masks_prompt_and_supervises_only_answer_and_end_of_turn(self):
        encoded = _encode_response_only(_messages(), self.tokenizer, max_length=100)
        supervised = [label for label in encoded["labels"] if label != _IGNORE_INDEX]

        self.assertEqual(supervised, self.tokenizer._content_ids("3") + [self.tokenizer.eos_token_id])
        first_target = encoded["labels"].index(supervised[0])
        self.assertTrue(all(label == _IGNORE_INDEX for label in encoded["labels"][:first_target]))
        self.assertEqual(encoded["input_ids"][first_target:], supervised)
        self.assertEqual(encoded["attention_mask"], [1] * len(encoded["input_ids"]))

    def test_accepts_model_tokenizer_returning_a_batch_encoding_shape(self):
        ids = apply_chat_template_ids(
            _DictReturningTokenizer(),
            _messages(),
            add_generation_prompt=False,
        )

        self.assertEqual(
            ids[-3:],
            self.tokenizer._content_ids("3") + [self.tokenizer.eos_token_id, 98],
        )

    def test_supports_keep_system_and_no_system_profiles(self):
        for with_system in (True, False):
            with self.subTest(with_system=with_system):
                encoded = _encode_response_only(
                    _messages(with_system=with_system),
                    self.tokenizer,
                    max_length=100,
                )
                supervised = [label for label in encoded["labels"] if label != _IGNORE_INDEX]
                self.assertEqual(
                    supervised,
                    self.tokenizer._content_ids("3") + [self.tokenizer.eos_token_id],
                )

    def test_truncates_prompt_from_left_without_truncating_target(self):
        encoded = _encode_response_only(
            _messages(user_content="x" * 100),
            self.tokenizer,
            max_length=8,
        )
        supervised = [label for label in encoded["labels"] if label != _IGNORE_INDEX]

        self.assertEqual(len(encoded["input_ids"]), 8)
        self.assertEqual(supervised, self.tokenizer._content_ids("3") + [self.tokenizer.eos_token_id])
        self.assertEqual(encoded["input_ids"][-2:], supervised)

    def test_rejects_a_limit_that_cannot_hold_the_complete_target(self):
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            _encode_response_only(_messages(), self.tokenizer, max_length=1)

    def test_chat_full_supervises_every_retained_token(self):
        encoded = _encode_chat_full(_messages(), self.tokenizer, max_length=100)

        self.assertEqual(encoded["labels"], encoded["input_ids"])
        self.assertIn(self.tokenizer._role_ids["system"], encoded["input_ids"])
        self.assertEqual(
            encoded["input_ids"][-2:],
            self.tokenizer._content_ids("3") + [self.tokenizer.eos_token_id],
        )

    def test_chat_full_truncation_still_preserves_complete_target(self):
        encoded = _encode_chat_full(
            _messages(user_content="x" * 100),
            self.tokenizer,
            max_length=8,
        )

        self.assertEqual(len(encoded["input_ids"]), 8)
        self.assertEqual(encoded["labels"], encoded["input_ids"])
        self.assertEqual(
            encoded["input_ids"][-2:],
            self.tokenizer._content_ids("3") + [self.tokenizer.eos_token_id],
        )

    def test_rejects_unknown_loss_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported loss mode"):
            _encode_training_example(_messages(), self.tokenizer, 100, "unknown")

    def test_padding_is_masked_in_labels(self):
        short = _encode_response_only(_messages(with_system=False), self.tokenizer, max_length=100)
        long = _encode_response_only(
            _messages(with_system=False, user_content="longer job"),
            self.tokenizer,
            max_length=100,
        )
        batch = _CausalLMCollator(self.tokenizer)([short, long])
        short_padding = len(long["input_ids"]) - len(short["input_ids"])

        self.assertGreater(short_padding, 0)
        self.assertEqual(batch["attention_mask"][0, -short_padding:].tolist(), [0] * short_padding)
        self.assertEqual(
            batch["labels"][0, -short_padding:].tolist(),
            [_IGNORE_INDEX] * short_padding,
        )
        self.assertEqual(batch["input_ids"].shape, batch["labels"].shape)

    def test_cli_exposes_both_loss_modes_and_defaults_to_response_only(self):
        parser = build_parser()

        default_args = parser.parse_args(["train"])
        self.assertEqual(default_args.loss_mode, "response-only")
        self.assertEqual(LOSS_MODES, ("response-only", "chat-full"))
        for loss_mode in LOSS_MODES:
            with self.subTest(loss_mode=loss_mode):
                args = parser.parse_args(["train", "--loss-mode", loss_mode])
                self.assertEqual(args.loss_mode, loss_mode)

    def test_cli_defaults_to_qwen25_and_accepts_qwen35_profile(self):
        parser = build_parser()

        default_args = parser.parse_args(["train"])
        self.assertEqual(default_args.model_profile, DEFAULT_MODEL_PROFILE)
        qwen35_args = parser.parse_args(["train", "--model-profile", "qwen35-2b"])
        self.assertEqual(qwen35_args.model_profile, "qwen35-2b")

    def test_qwen35_profile_uses_original_hf_weights_and_text_loader(self):
        profile = resolve_model_profile("qwen35-2b")

        self.assertEqual(profile.hf_id, "Qwen/Qwen3.5-2B")
        self.assertEqual(profile.ollama_tag, "qwen3.5:2b")
        self.assertEqual(profile.loader, "qwen3.5-text-causal-lm")
        self.assertEqual(profile.revision, "15852e8c16360a2fea060d615a32b45270f8a8fc")
        self.assertEqual(profile.torch_dtype, "bfloat16")
        self.assertEqual(profile.attention_implementation, "eager")
        self.assertFalse(profile.thinking)
        self.assertNotIn("conv1d", profile.lora_target_modules)

    def test_custom_hf_repository_does_not_reuse_profile_commit(self):
        unpinned = resolve_model_profile(
            "qwen35-2b",
            hf_id="example/custom-qwen",
        )
        pinned = resolve_model_profile(
            "qwen35-2b",
            hf_id="example/custom-qwen",
            revision="custom-commit",
        )

        self.assertEqual(unpinned.revision, "main")
        self.assertEqual(pinned.revision, "custom-commit")

    def test_qwen35_loader_uses_text_only_causal_class(self):
        import torch

        profile = resolve_model_profile("qwen35-2b")
        fake_class = SimpleNamespace(
            from_pretrained=lambda model_id, **kwargs: ("qwen35", model_id, kwargs)
        )

        with patch.dict(
            sys.modules,
            {"transformers": SimpleNamespace(Qwen3_5ForCausalLM=fake_class)},
        ):
            loaded = load_causal_lm(profile)

        self.assertEqual(
            loaded,
            (
                "qwen35",
                "Qwen/Qwen3.5-2B",
                {
                    "revision": "15852e8c16360a2fea060d615a32b45270f8a8fc",
                    "dtype": torch.bfloat16,
                    "attn_implementation": "eager",
                },
            ),
        )

    def test_model_profiles_are_loaded_from_the_training_contract(self):
        with CONTRACT_PATH.open("r", encoding="utf-8") as handle:
            contract = json.load(handle)

        self.assertEqual(set(MODEL_PROFILES), set(contract["model_profiles"]))
        for name, profile in MODEL_PROFILES.items():
            with self.subTest(profile=name):
                self.assertEqual(profile.hf_id, contract["model_profiles"][name]["hf_id"])
                self.assertEqual(
                    list(profile.lora_target_modules),
                    contract["model_profiles"][name]["lora_target_modules"],
                )

    def test_runtime_detects_intel_xpu_transformers_path(self):
        import torch

        importable = {"torch", "trl", "transformers", "peft"}
        with (
            patch(
                "src.python.ai_scorer.training.fine_tune_runtime._is_importable",
                side_effect=lambda name: name in importable,
            ),
            patch.object(torch.cuda, "is_available", return_value=False),
            patch.object(torch.xpu, "is_available", return_value=True),
            patch.object(torch.xpu, "get_device_name", return_value="Intel Arc"),
        ):
            runtime = detect_runtime()

        self.assertEqual(runtime.selected_path, "xpu-transformers-peft")
        self.assertEqual(runtime.xpu_device, "Intel Arc")
        self.assertEqual(runtime.warning, "")

    def test_runtime_detects_cuda_transformers_path_without_unsloth(self):
        import torch

        importable = {"torch", "transformers", "peft"}
        with (
            patch(
                "src.python.ai_scorer.training.fine_tune_runtime._is_importable",
                side_effect=lambda name: name in importable,
            ),
            patch.object(torch.cuda, "is_available", return_value=True),
            patch.object(torch.xpu, "is_available", return_value=False),
        ):
            runtime = detect_runtime()

        self.assertEqual(runtime.selected_path, "cuda-transformers-peft")
        self.assertEqual(runtime.warning, "")

    def test_qwen35_lora_targets_cover_both_attention_types_and_mlp(self):
        profile = resolve_model_profile("qwen35-2b")
        model = SimpleNamespace(
            named_modules=lambda: [
                (f"model.layers.0.linear_attn.{name}", object())
                for name in ("in_proj_qkv", "in_proj_z", "in_proj_a", "in_proj_b", "out_proj")
            ]
            + [
                (f"model.layers.3.self_attn.{name}", object())
                for name in ("q_proj", "k_proj", "v_proj", "o_proj")
            ]
            + [
                (f"model.layers.0.mlp.{name}", object())
                for name in ("gate_proj", "up_proj", "down_proj")
            ]
        )

        self.assertEqual(resolve_lora_target_modules(model, profile), list(profile.lora_target_modules))

    def test_lora_target_validation_rejects_architecture_mismatch(self):
        profile = resolve_model_profile("qwen35-2b")
        model = SimpleNamespace(named_modules=lambda: [("model.layers.0.mlp.gate_proj", object())])

        with self.assertRaisesRegex(ValueError, "missing configured LoRA targets"):
            resolve_lora_target_modules(model, profile)

    def test_merge_profile_is_loaded_from_new_and_legacy_run_manifests(self):
        import tempfile

        for base_model in (
            {
                "profile": "qwen35-2b",
                "hf_id": "Qwen/Qwen3.5-2B",
                "ollama_tag": "qwen3.5:2b",
            },
            {
                "hf_id": "Qwen/Qwen2.5-1.5B-Instruct",
                "ollama_tag": "qwen2.5:1.5b",
            },
        ):
            with self.subTest(base_model=base_model), tempfile.TemporaryDirectory() as directory:
                with open(os.path.join(directory, "run_manifest.json"), "w", encoding="utf-8") as handle:
                    json.dump({"base_model": base_model}, handle)

                profile = resolve_run_model_profile(directory)

                self.assertEqual(profile.hf_id, base_model["hf_id"])

    def test_run_profile_can_be_reconstructed_after_registry_rename(self):
        import tempfile

        stored = resolve_model_profile("qwen35-2b").manifest_dict()
        stored["profile"] = "retired-qwen35-profile"
        stored.pop("name")
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "run_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"base_model": stored}, handle)

            profile = resolve_run_model_profile(directory)

        self.assertEqual(profile.name, "retired-qwen35-profile")
        self.assertEqual(profile.revision, stored["revision"])

    def test_explicit_matching_profile_preserves_run_model_identity(self):
        import tempfile

        stored = resolve_model_profile("qwen35-2b").manifest_dict()
        stored["profile"] = stored.pop("name")
        stored["hf_id"] = "example/custom-qwen"
        stored["revision"] = "custom-commit"
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "run_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"base_model": stored}, handle)

            profile = resolve_run_model_profile(
                directory,
                profile_name="qwen35-2b",
            )

        self.assertEqual(profile.hf_id, "example/custom-qwen")
        self.assertEqual(profile.revision, "custom-commit")

    def test_explicit_profile_preserves_legacy_custom_model_identity(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "run_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "base_model": {
                            "hf_id": "example/legacy-custom-qwen",
                            "revision": "legacy-commit",
                        }
                    },
                    handle,
                )

            profile = resolve_run_model_profile(
                directory,
                profile_name="qwen35-2b",
            )

        self.assertEqual(profile.hf_id, "example/legacy-custom-qwen")
        self.assertEqual(profile.revision, "legacy-commit")

    def test_resolved_training_configuration_records_material_defaults(self):
        configuration = _resolved_training_configuration(
            resolve_model_profile("qwen35-2b"),
            per_device_batch_size=1,
            gradient_accumulation_steps=8,
            learning_rate=1e-4,
            max_steps=-1,
            num_train_epochs=25,
            seed=42,
        )

        self.assertEqual(configuration["lora"]["r"], 8)
        self.assertEqual(configuration["training_arguments"]["save_steps"], 50)
        self.assertEqual(
            configuration["training_arguments"]["optim"],
            "adamw_torch_fused",
        )
        self.assertEqual(configuration["model"]["torch_dtype"], "bfloat16")

    def test_cli_defaults_to_balanced_sampling_and_allows_unbalanced_baseline(self):
        parser = build_parser()

        default_args = parser.parse_args(["train"])
        self.assertEqual(default_args.sampling_mode, BALANCED_MODE)
        self.assertEqual(default_args.samples_per_job_preference, 1)
        self.assertEqual(default_args.samples_per_label, 0)
        self.assertEqual(default_args.na_share, 0.05)
        self.assertEqual(
            SAMPLING_MODES,
            ("label-preference-balanced", "job-preference-balanced", "all"),
        )

        baseline = parser.parse_args(["train", "--sampling-mode", "all"])
        self.assertEqual(baseline.sampling_mode, "all")

    def test_cli_accepts_explicit_cpu_thread_counts(self):
        args = build_parser().parse_args(
            ["train", "--cpu-threads", "22", "--cpu-interop-threads", "1"]
        )

        self.assertEqual(args.cpu_threads, 22)
        self.assertEqual(args.cpu_interop_threads, 1)

        with patch(
            "src.python.ai_scorer.training.fine_tune_train.main",
            return_value=0,
        ) as train_main:
            self.assertEqual(_cmd_train(args), 0)

        forwarded = train_main.call_args.args[0]
        self.assertEqual(
            forwarded[forwarded.index("--model-profile") + 1],
            DEFAULT_MODEL_PROFILE,
        )
        self.assertEqual(forwarded[forwarded.index("--cpu-threads") + 1], "22")
        self.assertEqual(forwarded[forwarded.index("--cpu-interop-threads") + 1], "1")
        self.assertEqual(forwarded[forwarded.index("--sampling-mode") + 1], BALANCED_MODE)
        self.assertEqual(
            forwarded[forwarded.index("--samples-per-job-preference") + 1],
            "1",
        )
        self.assertEqual(forwarded[forwarded.index("--samples-per-label") + 1], "0")
        self.assertEqual(forwarded[forwarded.index("--na-share") + 1], "0.05")

    def test_cli_forwards_explicit_label_overwrite_flag(self):
        args = build_parser().parse_args(["label", "--overwrite-labels"])

        with patch(
            "src.python.ai_scorer.training.labeler.main",
            return_value=None,
        ) as label_main:
            self.assertEqual(_cmd_label(args), 0)

        self.assertIn("--overwrite-labels", label_main.call_args.args[0])

    def test_package_defaults_to_runtime_scoring_instruction(self):
        args = build_parser().parse_args(
            ["package", "--run-dir", "run", "--ollama-tag", "scorer:test"]
        )

        self.assertEqual(args.system_prompt, SCORING_SYSTEM_INSTRUCTION)

    def test_modelfile_embeds_runtime_scoring_instruction(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Modelfile")
            _write_modelfile(path, "model-f16.gguf", SCORING_SYSTEM_INSTRUCTION)

            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()

            self.assertIn(f'SYSTEM """{SCORING_SYSTEM_INSTRUCTION}"""', content)
            self.assertTrue(content.startswith("FROM ./model-f16.gguf\n"))
            self.assertIn("\nPARAMETER temperature 0\nSYSTEM \"\"\"", content)

    def test_ollama_create_resolves_modelfile_before_changing_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            modelfile_path = os.path.join(directory, "Modelfile")
            relative_modelfile_path = os.path.relpath(modelfile_path)

            with patch(
                "src.python.ai_scorer.training.fine_tune_package.shutil.which",
                return_value="/usr/bin/ollama",
            ), patch(
                "src.python.ai_scorer.training.fine_tune_package._run"
            ) as run:
                _ollama_create("scorer:test", relative_modelfile_path, directory)

            run.assert_called_once_with(
                ["ollama", "create", "scorer:test", "-f", os.path.abspath(modelfile_path)],
                cwd=directory,
            )

    def test_configures_torch_openmp_and_mkl_threads(self):
        state = {"threads": 96, "interop_threads": 96}
        fake_torch = SimpleNamespace(
            set_num_threads=lambda value: state.update(threads=value),
            set_num_interop_threads=lambda value: state.update(interop_threads=value),
            get_num_threads=lambda: state["threads"],
            get_num_interop_threads=lambda: state["interop_threads"],
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "CPU_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OMP_DYNAMIC",
                "OMP_PROC_BIND",
                "OMP_PLACES",
            }
        }

        with patch.dict(os.environ, environment, clear=True), patch.dict(
            sys.modules, {"torch": fake_torch}
        ):
            configured = _configure_cpu_runtime(22, 1)

            self.assertEqual(os.environ["CPU_THREADS"], "22")
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "22")
            self.assertEqual(os.environ["MKL_NUM_THREADS"], "22")
            self.assertEqual(os.environ["OMP_DYNAMIC"], "FALSE")
            self.assertEqual(os.environ["OMP_PROC_BIND"], "spread")
            self.assertEqual(os.environ["OMP_PLACES"], "threads")

        self.assertTrue(configured["configured"])
        self.assertEqual(configured["torch_threads"], 22)
        self.assertEqual(configured["torch_interop_threads"], 1)


class CheckpointValidationTests(unittest.TestCase):
    @staticmethod
    def _prediction(case_id, expected, actual, error=None):
        return {
            "case_id": case_id,
            "expected_score": expected if expected != "N/A" else None,
            "expected_score_available": expected != "N/A",
            "actual_score": actual if actual != "N/A" else None,
            "actual_score_available": None if error else actual != "N/A",
            "raw_response": "" if error else str(actual),
            "error": error,
        }

    def test_parse_generated_score_requires_exact_contract(self):
        self.assertEqual(parse_generated_score(" 4 "), (4, True, None))
        self.assertEqual(parse_generated_score("N/A"), (None, False, None))
        self.assertIsNotNone(parse_generated_score("Score: 4")[2])

    def test_validation_summary_includes_within_one_accuracy(self):
        summary = build_validation_summary(
            50,
            [
                self._prediction("a", 3, 4),
                self._prediction("b", "N/A", "N/A"),
                self._prediction("c", 0, 5),
            ],
        )

        self.assertAlmostEqual(summary["metrics"]["exact_accuracy"], 1 / 3)
        self.assertAlmostEqual(summary["metrics"]["within_one_accuracy"], 2 / 3)

    def test_checkpoint_selection_uses_declared_lexicographic_order(self):
        summaries = [
            {"checkpoint_step": 50, "metrics": {"exact_accuracy": 0.5, "na_f1": 0.8, "mean_abs_error": 0.7}},
            {"checkpoint_step": 100, "metrics": {"exact_accuracy": 0.5, "na_f1": 1.0, "mean_abs_error": 0.8}},
            {"checkpoint_step": 150, "metrics": {"exact_accuracy": 0.6, "na_f1": 0.0, "mean_abs_error": 2.0}},
        ]

        self.assertEqual(select_checkpoint(summaries)["checkpoint_step"], 150)

    def test_malformed_output_cannot_beat_valid_numeric_output(self):
        malformed = build_validation_summary(
            50,
            [self._prediction("case", 3, None, error="invalid response")],
        )
        valid = build_validation_summary(
            100,
            [self._prediction("case", 3, 5)],
        )

        self.assertEqual(malformed["metrics"]["invalid_rate"], 1.0)
        self.assertEqual(malformed["metrics"]["numeric_coverage"], 0.0)
        self.assertEqual(select_checkpoint([malformed, valid])["checkpoint_step"], 100)

    def test_malformed_output_is_not_counted_as_predicted_na(self):
        summary = build_validation_summary(
            50,
            [self._prediction("case", "N/A", None, error="invalid response")],
        )

        self.assertEqual(summary["metrics"]["na_recall"], 0.0)

    def test_checkpoint_discovery_orders_numeric_steps(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            for name in ("checkpoint-100", "checkpoint-50", "not-a-checkpoint"):
                os.mkdir(os.path.join(directory, name))

            self.assertEqual(
                [step for step, _path in discover_checkpoints(directory)],
                [50, 100],
            )

    def test_checkpoint_validation_rejects_changed_dataset(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            run_dir = os.path.join(directory, "run")
            dataset_dir = os.path.join(directory, "dataset")
            os.mkdir(run_dir)
            os.mkdir(dataset_dir)
            for name in ("train.jsonl", "val.jsonl"):
                with open(os.path.join(dataset_dir, name), "w", encoding="utf-8") as handle:
                    handle.write("{}\n")
            with open(os.path.join(run_dir, "run_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"dataset_hash": "stale"}, handle)

            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_run_dataset_hash(run_dir, dataset_dir)

    def test_checkpoint_validation_resolves_dataset_from_run_manifest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            dataset_dir = os.path.join(directory, "no-system")
            with open(os.path.join(directory, "run_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"dataset_dir": dataset_dir, "dataset_profile": "no-system"}, handle)

            self.assertEqual(resolve_run_dataset_dir(directory), dataset_dir)

    def test_merge_defaults_to_selected_validation_checkpoint(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = os.path.join(directory, "checkpoint-100")
            os.mkdir(checkpoint_dir)
            with open(
                os.path.join(directory, "checkpoint_validation.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "selected_checkpoint_step": 100,
                        "selected_checkpoint_dir": checkpoint_dir,
                    },
                    handle,
                )

            self.assertEqual(resolve_adapter_dir(directory), checkpoint_dir)


if __name__ == "__main__":
    unittest.main()
