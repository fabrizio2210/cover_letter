from __future__ import annotations

import unittest

from src.python.ai_scorer.training.fine_tune_train import (
    LOSS_MODES,
    _CausalLMCollator,
    _IGNORE_INDEX,
    _encode_chat_full,
    _encode_response_only,
    _encode_training_example,
)
from src.python.ai_scorer.training.cli import build_parser


class _FakeQwenTokenizer:
    eos_token_id = 99
    pad_token_id = 0
    padding_side = "right"

    _role_ids = {"system": 10, "user": 11, "assistant": 12}

    @staticmethod
    def _content_ids(content: str) -> list[int]:
        return [100 + ord(character) for character in content]

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        if not tokenize:
            raise AssertionError("The encoder must request tokenized chat-template output")
        ids: list[int] = []
        for message in messages:
            ids.extend([self._role_ids[message["role"]]])
            ids.extend(self._content_ids(message["content"]))
            ids.extend([self.eos_token_id, 98])
        if add_generation_prompt:
            ids.append(self._role_ids["assistant"])
        return ids


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


if __name__ == "__main__":
    unittest.main()
