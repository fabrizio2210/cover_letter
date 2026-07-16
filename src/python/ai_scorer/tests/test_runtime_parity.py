from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.python.ai_scorer.training.runtime_parity import (
    _messages_for_profile,
    _pair_summary,
    _parsed_prediction,
    _read_modelfile_system,
    _summarize,
)


class RuntimeParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = [
            {"role": "system", "content": "Detailed scoring rubric"},
            {"role": "user", "content": "Score this job"},
            {"role": "assistant", "content": "3"},
        ]

    def test_request_system_keeps_dataset_instruction_and_removes_target(self) -> None:
        actual = _messages_for_profile(
            self.messages,
            "request-system",
            "Package default",
            for_ollama=True,
        )
        self.assertEqual(
            actual,
            [
                {"role": "system", "content": "Detailed scoring rubric"},
                {"role": "user", "content": "Score this job"},
            ],
        )

    def test_package_default_is_explicit_only_for_hf(self) -> None:
        hf_messages = _messages_for_profile(
            self.messages,
            "package-default",
            "Package default",
            for_ollama=False,
        )
        ollama_messages = _messages_for_profile(
            self.messages,
            "package-default",
            "Package default",
            for_ollama=True,
        )
        self.assertEqual(hf_messages[0], {"role": "system", "content": "Package default"})
        self.assertEqual(ollama_messages, [{"role": "user", "content": "Score this job"}])

    def test_reads_packaged_system_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Modelfile"
            path.write_text(
                'FROM ./model.gguf\nSYSTEM """Package default\nsecond line"""\n',
                encoding="utf-8",
            )
            self.assertEqual(_read_modelfile_system(str(path)), "Package default\nsecond line")

    def test_summaries_and_pair_agreement_use_parsed_labels(self) -> None:
        left = [_parsed_prediction("3"), _parsed_prediction("N/A")]
        right = [_parsed_prediction("3"), _parsed_prediction("0")]
        for prediction in left + right:
            prediction["latency_ms"] = 1.0

        summary = _summarize(["3", "N/A"], left)
        pair = _pair_summary(left, right)

        self.assertEqual(summary["exact"], 2)
        self.assertEqual(summary["distribution"], {"3": 1, "N/A": 1})
        self.assertEqual(pair["matches"], 1)
        self.assertEqual(pair["mismatch_indexes"], [1])


if __name__ == "__main__":
    unittest.main()
