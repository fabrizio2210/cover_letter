from __future__ import annotations

import argparse
import json
import os
import random
import time
from dataclasses import asdict

from src.python.ai_scorer.training.fine_tune_manifest import (
    collect_jsonl_paths,
    current_git_sha,
    now_epoch,
    tree_sha256,
    write_manifest,
)
from src.python.ai_scorer.training.fine_tune_preflight import run_preflight
from src.python.ai_scorer.training.fine_tune_runtime import detect_runtime


def _resolve_dataset_dir(profile: str, override: str) -> str:
    if override:
        return override
    if profile == "keep-system":
        return "src/python/ai_scorer/training/data/export"
    return "src/python/ai_scorer/training/data/export/no-system-prompt"


def _run_id() -> str:
    return time.strftime("run-%Y%m%d-%H%M%S")


def _read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


_IGNORE_INDEX = -100
LOSS_MODES = ("response-only", "chat-full")


def _encode_training_example(
    messages: list[dict],
    tokenizer,
    max_length: int,
    loss_mode: str,
) -> dict[str, list[int]]:
    """Apply the model chat template and build labels for the selected loss mode."""
    if max_length <= 0:
        raise ValueError("max_length must be greater than zero")
    if loss_mode not in LOSS_MODES:
        raise ValueError(f"Unsupported loss mode: {loss_mode}")
    if not messages or str(messages[-1].get("role", "")).strip() != "assistant":
        raise ValueError("Each training example must end with an assistant message")

    prompt_messages = messages[:-1]
    prompt_ids = list(
        tokenizer.apply_chat_template(
            prompt_messages,
            tokenize=True,
            add_generation_prompt=True,
        )
    )
    full_ids = list(
        tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=False,
        )
    )
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("Chat template did not produce a stable assistant prompt prefix")

    response_ids = full_ids[len(prompt_ids) :]
    end_token_id = tokenizer.eos_token_id
    if end_token_id is None:
        raise ValueError("Tokenizer must define an end-of-turn/EOS token")
    try:
        end_index = response_ids.index(end_token_id)
    except ValueError as exc:
        raise ValueError("Assistant response is missing the chat-template end-of-turn token") from exc

    # Qwen's template adds a newline after <|im_end|>. Stop at <|im_end|> so
    # the only supervised tokens are the score and the end-of-turn marker.
    response_ids = response_ids[: end_index + 1]
    if len(response_ids) == 1:
        raise ValueError("Assistant response must contain a target before the end-of-turn token")
    if len(response_ids) > max_length:
        raise ValueError("Assistant target cannot fit within max_length without truncation")

    # Reserve space for the complete response before truncating context. Keeping
    # the right edge of the prompt also preserves Qwen's assistant-turn header.
    prompt_budget = max_length - len(response_ids)
    if len(prompt_ids) > prompt_budget:
        prompt_ids = prompt_ids[-prompt_budget:] if prompt_budget else []

    input_ids = prompt_ids + response_ids
    if loss_mode == "response-only":
        labels = [_IGNORE_INDEX] * len(prompt_ids) + response_ids
    else:
        labels = list(input_ids)
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * len(input_ids),
        "labels": labels,
    }


def _encode_response_only(messages: list[dict], tokenizer, max_length: int) -> dict[str, list[int]]:
    """Apply the model chat template and supervise only the final assistant turn."""
    return _encode_training_example(messages, tokenizer, max_length, "response-only")


def _encode_chat_full(messages: list[dict], tokenizer, max_length: int) -> dict[str, list[int]]:
    """Apply the model chat template and supervise every retained non-padding token."""
    return _encode_training_example(messages, tokenizer, max_length, "chat-full")


class _CausalLMCollator:
    """Pad model inputs while always masking padding positions in labels."""

    def __init__(self, tokenizer):
        self.pad_token_id = tokenizer.pad_token_id
        self.padding_side = tokenizer.padding_side
        if self.pad_token_id is None:
            raise ValueError("Tokenizer must define a pad token")
        if self.padding_side not in {"left", "right"}:
            raise ValueError(f"Unsupported tokenizer padding side: {self.padding_side}")

    def __call__(self, features: list[dict]):
        import torch  # type: ignore

        max_length = max(len(feature["input_ids"]) for feature in features)
        batch = {"input_ids": [], "attention_mask": [], "labels": []}
        for feature in features:
            pad_length = max_length - len(feature["input_ids"])
            if self.padding_side == "right":
                batch["input_ids"].append(feature["input_ids"] + [self.pad_token_id] * pad_length)
                batch["attention_mask"].append(feature["attention_mask"] + [0] * pad_length)
                batch["labels"].append(feature["labels"] + [_IGNORE_INDEX] * pad_length)
            else:
                batch["input_ids"].append([self.pad_token_id] * pad_length + feature["input_ids"])
                batch["attention_mask"].append([0] * pad_length + feature["attention_mask"])
                batch["labels"].append([_IGNORE_INDEX] * pad_length + feature["labels"])
        return {name: torch.tensor(values, dtype=torch.long) for name, values in batch.items()}


def _set_determinism(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np  # type: ignore

        np.random.seed(seed)
    except Exception:
        pass

    try:
        import torch  # type: ignore

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def _train_cpu_transformers(
    base_model_hf: str,
    train_path: str,
    val_path: str,
    output_dir: str,
    per_device_batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    max_steps: int,
    num_train_epochs: int,
    max_seq_length: int,
    seed: int,
    loss_mode: str,
    resume_from_checkpoint: str,
) -> dict:
    try:
        import torch  # type: ignore
        from peft import LoraConfig, get_peft_model  # type: ignore
        from transformers import (  # type: ignore
            AutoModelForCausalLM,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except Exception as exc:
        raise RuntimeError(
            "CPU fallback requires transformers, peft, and torch. Install training deps before running."
        ) from exc

    class _TextDataset(torch.utils.data.Dataset):
        def __init__(self, rows: list[dict], tokenizer, max_len: int, selected_loss_mode: str):
            self.examples = []
            for row in rows:
                self.examples.append(
                    _encode_training_example(
                        list(row.get("messages", [])),
                        tokenizer,
                        max_len,
                        selected_loss_mode,
                    )
                )

        def __len__(self):
            return len(self.examples)

        def __getitem__(self, idx):
            return self.examples[idx]

    tokenizer = AutoTokenizer.from_pretrained(base_model_hf, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(base_model_hf)
    lora_config = LoraConfig(
        r=8,
        lora_alpha=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    train_rows = _read_jsonl(train_path)
    val_rows = _read_jsonl(val_path)

    train_ds = _TextDataset(train_rows, tokenizer, max_seq_length, loss_mode)
    val_ds = _TextDataset(val_rows, tokenizer, max_seq_length, loss_mode)

    collator = _CausalLMCollator(tokenizer)
    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=per_device_batch_size,
        per_device_eval_batch_size=per_device_batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        learning_rate=learning_rate,
        max_steps=max_steps,
        num_train_epochs=num_train_epochs,
        logging_steps=10,
        save_steps=50,
        eval_steps=50,
        eval_strategy="steps",
        save_strategy="steps",
        report_to=[],
        seed=seed,
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=collator,
        processing_class=tokenizer,
    )

    resume = resume_from_checkpoint if resume_from_checkpoint else None
    result = trainer.train(resume_from_checkpoint=resume)
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)

    metrics = dict(result.metrics)
    metrics.update({"train_records": len(train_rows), "val_records": len(val_rows)})
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fine-tuning launcher for ai_scorer")
    parser.add_argument("--base-model-ollama", default="qwen2.5:1.5b")
    parser.add_argument("--base-model-hf", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--dataset-profile", choices=["keep-system", "no-system"], default="keep-system")
    parser.add_argument("--dataset-dir", default="")
    parser.add_argument("--output-root", default="src/python/ai_scorer/training/artifacts")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--per-device-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--num-train-epochs", type=int, default=1)
    parser.add_argument("--loss-mode", choices=LOSS_MODES, default="response-only")
    parser.add_argument("--resume-from-checkpoint", default="")
    parser.add_argument("--smoke-run", action="store_true")
    args = parser.parse_args(argv)

    _set_determinism(args.seed)

    dataset_dir = _resolve_dataset_dir(args.dataset_profile, args.dataset_dir)
    train_path = os.path.join(dataset_dir, "train.jsonl")
    val_path = os.path.join(dataset_dir, "val.jsonl")
    if not os.path.isfile(train_path) or not os.path.isfile(val_path):
        raise SystemExit(f"Missing dataset split in {dataset_dir}; expected train.jsonl and val.jsonl")

    preflight = run_preflight(dataset_dir, ["train", "val"])
    if preflight.critical_error_count > 0:
        raise SystemExit("Preflight failed. Run training preflight command and fix dataset errors first.")

    runtime = detect_runtime()
    if runtime.warning:
        print(f"[training.train] WARNING: {runtime.warning}")

    run_id = args.run_id or _run_id()
    run_dir = os.path.join(args.output_root, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)

    manifest_path = os.path.join(run_dir, "run_manifest.json")
    pre_manifest = {
        "run_id": run_id,
        "base_model": {
            "ollama_tag": args.base_model_ollama,
            "hf_id": args.base_model_hf,
        },
        "runtime": asdict(runtime),
        "dataset_profile": args.dataset_profile,
        "dataset_dir": dataset_dir,
        "dataset_hash": tree_sha256(collect_jsonl_paths(dataset_dir)),
        "git_sha": current_git_sha(),
        "seed": args.seed,
        "max_seq_length": args.max_seq_length,
        "per_device_batch_size": args.per_device_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "learning_rate": args.learning_rate,
        "max_steps": 5 if args.smoke_run else args.max_steps,
        "num_train_epochs": args.num_train_epochs,
        "loss_mode": args.loss_mode,
        "resume_from_checkpoint": args.resume_from_checkpoint,
        "started_at_epoch": now_epoch(),
    }
    write_manifest(manifest_path, pre_manifest)

    started = time.time()

    max_steps = 5 if args.smoke_run else args.max_steps
    metrics = _train_cpu_transformers(
        base_model_hf=args.base_model_hf,
        train_path=train_path,
        val_path=val_path,
        output_dir=run_dir,
        per_device_batch_size=args.per_device_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        max_steps=max_steps,
        num_train_epochs=args.num_train_epochs,
        max_seq_length=args.max_seq_length,
        seed=args.seed,
        loss_mode=args.loss_mode,
        resume_from_checkpoint=args.resume_from_checkpoint,
    )

    elapsed = round(time.time() - started, 3)
    post_manifest = dict(pre_manifest)
    post_manifest["elapsed_seconds"] = elapsed
    post_manifest["completed_at_epoch"] = now_epoch()
    post_manifest["metrics"] = metrics
    post_manifest["path"] = {
        "run_dir": run_dir,
        "manifest": manifest_path,
    }
    write_manifest(manifest_path, post_manifest)

    print(f"[training.train] run_id={run_id}")
    print(f"[training.train] run_dir={run_dir}")
    print(f"[training.train] manifest={manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
