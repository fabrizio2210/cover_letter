from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from typing import Any

from src.python.ai_scorer.evals.metrics import CaseResult, compute_metrics
from src.python.ai_scorer.training.fine_tune_manifest import (
    collect_jsonl_paths,
    now_epoch,
    tree_sha256,
    write_manifest,
)
from src.python.ai_scorer.training.fine_tune_models import (
    MODEL_PROFILE_NAMES,
    ModelProfile,
    apply_chat_template_ids,
    load_causal_lm,
    load_tokenizer,
    resolve_run_model_profile,
)
from src.python.ai_scorer.training.fine_tune_runtime import resolve_torch_device


_CHECKPOINT_RE = re.compile(r"^checkpoint-(\d+)$")
_VALID_ANSWERS = {str(score) for score in range(6)} | {"N/A"}


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _read_run_manifest(run_dir: str) -> dict[str, Any]:
    manifest_path = os.path.join(run_dir, "run_manifest.json")
    if not os.path.isfile(manifest_path):
        raise ValueError(f"Run manifest does not exist: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_run_dataset_dir(run_dir: str, override: str = "") -> str:
    if override:
        return override
    manifest = _read_run_manifest(run_dir)
    dataset_dir = str(manifest.get("dataset_dir") or "")
    if not dataset_dir:
        profile = str(manifest.get("dataset_profile") or "keep-system")
        if profile == "no-system":
            return "src/python/ai_scorer/training/data/export/no-system-prompt"
        return "src/python/ai_scorer/training/data/export"
    return dataset_dir


def discover_checkpoints(run_dir: str) -> list[tuple[int, str]]:
    checkpoints: list[tuple[int, str]] = []
    for name in os.listdir(run_dir):
        match = _CHECKPOINT_RE.fullmatch(name)
        path = os.path.join(run_dir, name)
        if match and os.path.isdir(path):
            checkpoints.append((int(match.group(1)), path))
    return sorted(checkpoints)


def parse_generated_score(text: str) -> tuple[int | None, bool | None, str | None]:
    normalized = text.strip()
    if normalized not in _VALID_ANSWERS:
        return None, None, f"invalid response: {normalized!r}"
    if normalized == "N/A":
        return None, False, None
    return int(normalized), True, None


def _case_id(row: dict[str, Any]) -> str:
    case_id = row.get("case_id") or (row.get("meta") or {}).get("case_id")
    if not case_id:
        raise ValueError("Validation row is missing case_id and meta.case_id")
    return str(case_id)


def verify_run_dataset_hash(run_dir: str, dataset_dir: str) -> str:
    manifest = _read_run_manifest(run_dir)
    expected_hash = str(manifest.get("dataset_hash") or "")
    actual_hash = tree_sha256(collect_jsonl_paths(dataset_dir))
    if expected_hash and actual_hash != expected_hash:
        raise ValueError(
            "Current dataset hash does not match the training run manifest: "
            f"expected={expected_hash} actual={actual_hash}"
        )
    return actual_hash


def build_validation_summary(
    checkpoint_step: int,
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    results = [
        CaseResult(
            case_id=str(item["case_id"]),
            model=f"checkpoint-{checkpoint_step}",
            expected_score=item["expected_score"],
            expected_score_available=item["expected_score_available"],
            actual_score=item["actual_score"],
            actual_score_available=item["actual_score_available"],
            error=item["error"],
        )
        for item in predictions
    ]
    metrics = asdict(compute_metrics(results))
    invalid_count = sum(item["error"] is not None for item in predictions)
    valid_predicted_na = sum(
        item["error"] is None and item["actual_score_available"] is False
        for item in predictions
    )
    expected_na = sum(not item["expected_score_available"] for item in predictions)
    true_na = sum(
        item["error"] is None
        and item["actual_score_available"] is False
        and not item["expected_score_available"]
        for item in predictions
    )
    na_precision = true_na / valid_predicted_na if valid_predicted_na else 0.0
    na_recall = true_na / expected_na if expected_na else 0.0
    na_f1 = (
        2 * na_precision * na_recall / (na_precision + na_recall)
        if na_precision + na_recall
        else 0.0
    )

    expected_numeric = sum(item["expected_score_available"] for item in predictions)
    valid_numeric = sum(
        item["error"] is None
        and item["expected_score_available"]
        and item["actual_score_available"] is True
        and item["actual_score"] is not None
        for item in predictions
    )
    # Missing numeric answers, including N/A and malformed text, receive the
    # maximum 0..5 score error so low coverage cannot look like a low MAE.
    numeric_errors = []
    for item in predictions:
        if not item["expected_score_available"]:
            continue
        if (
            item["error"] is not None
            or item["actual_score_available"] is not True
            or item["actual_score"] is None
        ):
            numeric_errors.append(5)
        else:
            numeric_errors.append(abs(item["expected_score"] - item["actual_score"]))

    metrics.update(
        {
            "na_precision": na_precision,
            "na_recall": na_recall,
            "na_f1": na_f1,
            "mean_abs_error": (
                sum(numeric_errors) / len(numeric_errors) if numeric_errors else 0.0
            ),
            "invalid_count": invalid_count,
            "invalid_rate": invalid_count / len(predictions) if predictions else 0.0,
            "numeric_coverage": valid_numeric / expected_numeric if expected_numeric else 0.0,
        }
    )
    within_one = 0
    for item in predictions:
        if item["error"] is not None:
            continue
        if not item["expected_score_available"]:
            within_one += int(item["actual_score_available"] is False)
        elif (
            item["actual_score_available"]
            and item["actual_score"] is not None
            and abs(item["expected_score"] - item["actual_score"]) <= 1
        ):
            within_one += 1
    metrics["within_one_accuracy"] = within_one / len(predictions) if predictions else 0.0
    return {
        "checkpoint_step": checkpoint_step,
        "metrics": metrics,
        "predictions": predictions,
    }


def select_checkpoint(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        raise ValueError("No checkpoint validation summaries were provided")

    def rank(summary: dict[str, Any]) -> tuple[float, float, float, int]:
        metrics = summary["metrics"]
        return (
            float(metrics["exact_accuracy"]),
            float(metrics["na_f1"]),
            -float(metrics["mean_abs_error"]),
            -int(summary["checkpoint_step"]),
        )

    return max(summaries, key=rank)


def _expected_answer(row: dict[str, Any]) -> tuple[int | None, bool]:
    messages = list(row.get("messages") or [])
    if not messages or messages[-1].get("role") != "assistant":
        raise ValueError(f"Validation case {_case_id(row)!r} has no assistant target")
    answer = str(messages[-1].get("content") or "").strip()
    if answer not in _VALID_ANSWERS:
        raise ValueError(f"Validation case {_case_id(row)!r} has invalid target {answer!r}")
    return (None, False) if answer == "N/A" else (int(answer), True)


def _load_adapters(model_profile: ModelProfile, checkpoints: list[tuple[int, str]]):
    try:
        from peft import PeftModel  # type: ignore
    except Exception as exc:
        raise RuntimeError("Checkpoint validation requires peft") from exc

    base_model = load_causal_lm(model_profile)
    base_model.config.use_cache = True
    first_step, first_path = checkpoints[0]
    first_name = f"checkpoint_{first_step}"
    model = PeftModel.from_pretrained(base_model, first_path, adapter_name=first_name)
    for step, path in checkpoints[1:]:
        model.load_adapter(path, adapter_name=f"checkpoint_{step}")
    model.to(resolve_torch_device())
    model.eval()
    return model


def _evaluate_checkpoint(
    model,
    tokenizer,
    model_profile: ModelProfile,
    checkpoint_step: int,
    rows: list[dict[str, Any]],
    max_new_tokens: int,
) -> dict[str, Any]:
    import torch  # type: ignore

    model.set_adapter(f"checkpoint_{checkpoint_step}")
    predictions: list[dict[str, Any]] = []
    for row in rows:
        messages = list(row["messages"])
        expected_score, expected_available = _expected_answer(row)
        input_ids = apply_chat_template_ids(
            tokenizer,
            messages[:-1],
            add_generation_prompt=True,
            enable_thinking=model_profile.thinking,
        )
        tensor = torch.tensor([input_ids], dtype=torch.long, device=model.device)
        with torch.inference_mode():
            generated = model.generate(
                input_ids=tensor,
                attention_mask=torch.ones_like(tensor),
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        raw_response = tokenizer.decode(
            generated[0, tensor.shape[1] :],
            skip_special_tokens=True,
        ).strip()
        actual_score, actual_available, error = parse_generated_score(raw_response)
        predictions.append(
            {
                "case_id": _case_id(row),
                "expected_score": expected_score,
                "expected_score_available": expected_available,
                "actual_score": actual_score,
                "actual_score_available": actual_available,
                "raw_response": raw_response,
                "error": error,
            }
        )
    return build_validation_summary(checkpoint_step, predictions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate and select fine-tuning checkpoints")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--dataset-dir",
        default="",
        help="Override the dataset directory recorded in run_manifest.json",
    )
    parser.add_argument("--model-profile", choices=MODEL_PROFILE_NAMES, default="")
    parser.add_argument("--base-model-hf", default="")
    parser.add_argument("--base-model-revision", default="")
    parser.add_argument("--max-new-tokens", type=int, default=8)
    args = parser.parse_args(argv)

    if args.max_new_tokens <= 0:
        raise SystemExit("--max-new-tokens must be greater than zero")
    checkpoints = discover_checkpoints(args.run_dir)
    if not checkpoints:
        raise SystemExit(f"No checkpoint-* directories found under {args.run_dir}")
    try:
        dataset_dir = resolve_run_dataset_dir(args.run_dir, args.dataset_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    val_path = os.path.join(dataset_dir, "val.jsonl")
    if not os.path.isfile(val_path):
        raise SystemExit(f"Validation split does not exist: {val_path}")

    try:
        dataset_hash = verify_run_dataset_hash(args.run_dir, dataset_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    model_profile = resolve_run_model_profile(
        args.run_dir,
        profile_name=args.model_profile,
        hf_id=args.base_model_hf,
        revision=args.base_model_revision,
    )
    tokenizer = load_tokenizer(model_profile)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = _load_adapters(model_profile, checkpoints)
    rows = _read_jsonl(val_path)

    summaries = []
    for step, _path in checkpoints:
        print(f"[training.validate] checkpoint={step} cases={len(rows)}")
        summaries.append(
            _evaluate_checkpoint(
                model,
                tokenizer,
                model_profile,
                step,
                rows,
                args.max_new_tokens,
            )
        )
    selected = select_checkpoint(summaries)
    output_path = os.path.join(args.run_dir, "checkpoint_validation.json")
    write_manifest(
        output_path,
        {
            "run_dir": args.run_dir,
            "model_profile": model_profile.manifest_dict(),
            "dataset_dir": dataset_dir,
            "dataset_hash": dataset_hash,
            "max_new_tokens": args.max_new_tokens,
            "checkpoints": summaries,
            "selected_checkpoint_step": selected["checkpoint_step"],
            "selected_checkpoint_dir": os.path.join(
                args.run_dir, f"checkpoint-{selected['checkpoint_step']}"
            ),
            "selection_order": [
                "exact_accuracy descending",
                "na_f1 descending",
                "mean_abs_error ascending",
                "checkpoint_step ascending",
            ],
            "completed_at_epoch": now_epoch(),
        },
    )
    print(f"[training.validate] selected_checkpoint={selected['checkpoint_step']}")
    print(f"[training.validate] output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
