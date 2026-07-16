"""Compare merged Hugging Face and packaged Ollama scorer predictions.

The diagnostic runs the same exported validation prompts through both runtimes
under two prompt profiles:

* request-system: send the detailed system message stored in the dataset;
* package-default: omit that message and rely on the packaged Modelfile SYSTEM.

For the Hugging Face side, package-default is emulated by replacing the
dataset system message with the Modelfile SYSTEM text. This keeps the effective
conversation aligned across runtimes instead of comparing a no-system HF prompt
with a default-system Ollama prompt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from typing import Any

from src.python.ai_scorer.ai_scorer import (
    build_ollama_client,
    extract_ollama_content,
    parse_ollama_response,
)


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
    if not rows:
        raise ValueError(f"No validation rows found in {path}")
    return rows


def _read_modelfile_system(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    match = re.search(r'^\s*SYSTEM\s+"""(.*?)"""\s*$', content, flags=re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"Modelfile does not contain a triple-quoted SYSTEM instruction: {path}")
    return match.group(1).strip()


def _messages_for_profile(
    messages: list[dict[str, Any]],
    profile: str,
    package_system: str,
    *,
    for_ollama: bool,
) -> list[dict[str, str]]:
    without_system = [
        {"role": str(message.get("role", "")), "content": str(message.get("content", ""))}
        for message in messages
        if str(message.get("role", "")) != "system"
    ]
    if profile == "request-system":
        return [
            {"role": str(message.get("role", "")), "content": str(message.get("content", ""))}
            for message in messages
            if str(message.get("role", "")) != "assistant"
        ]
    if profile != "package-default":
        raise ValueError(f"Unsupported prompt profile: {profile}")

    without_assistant = [message for message in without_system if message["role"] != "assistant"]
    if for_ollama:
        # The registered model supplies this instruction from its Modelfile.
        return without_assistant
    return [{"role": "system", "content": package_system}, *without_assistant]


def _parsed_prediction(content: str) -> dict[str, Any]:
    score, available, strategy = parse_ollama_response(content)
    error = None
    if available is None:
        error = "unparseable_response"
    elif available and (score is None or score < 0 or score > 5):
        error = "score_out_of_range"
    return {
        "content": content,
        "score": score,
        "score_available": available,
        "parse_strategy": strategy,
        "error": error,
    }


def _prediction_label(prediction: dict[str, Any]) -> str:
    if prediction.get("error"):
        return "error"
    if prediction.get("score_available") is False:
        return "N/A"
    return str(prediction.get("score"))


def _expected_label(row: dict[str, Any]) -> str:
    messages = list(row.get("messages", []))
    if not messages or str(messages[-1].get("role", "")) != "assistant":
        raise ValueError("Each validation row must end with an assistant target")
    parsed = _parsed_prediction(str(messages[-1].get("content", "")))
    if parsed["error"]:
        raise ValueError(f"Invalid validation target: {messages[-1].get('content')!r}")
    return _prediction_label(parsed)


def _run_hf(
    rows: list[dict[str, Any]],
    merged_dir: str,
    profile: str,
    package_system: str,
    max_seq_length: int,
    max_new_tokens: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    import torch  # type: ignore
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

    tokenizer = AutoTokenizer.from_pretrained(merged_dir, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(merged_dir)
    model.eval()

    prompts = [
        tokenizer.apply_chat_template(
            _messages_for_profile(
                list(row.get("messages", [])),
                profile,
                package_system,
                for_ollama=False,
            ),
            tokenize=False,
            add_generation_prompt=True,
        )
        for row in rows
    ]

    predictions: list[dict[str, Any]] = []
    for start in range(0, len(prompts), batch_size):
        batch_prompts = prompts[start : start + batch_size]
        encoded = tokenizer(
            batch_prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=max_seq_length,
        )
        input_width = int(encoded["input_ids"].shape[1])
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                do_sample=False,
                max_new_tokens=max_new_tokens,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        decoded = tokenizer.batch_decode(generated[:, input_width:], skip_special_tokens=True)
        per_item_ms = elapsed_ms / len(decoded)
        for content in decoded:
            prediction = _parsed_prediction(content)
            prediction["latency_ms"] = per_item_ms
            predictions.append(prediction)
    return predictions


def _run_ollama(
    rows: list[dict[str, Any]],
    ollama_host: str,
    ollama_model: str,
    profile: str,
    package_system: str,
) -> list[dict[str, Any]]:
    client = build_ollama_client(ollama_host)
    predictions: list[dict[str, Any]] = []
    for row in rows:
        messages = _messages_for_profile(
            list(row.get("messages", [])),
            profile,
            package_system,
            for_ollama=True,
        )
        started = time.perf_counter()
        response = client.chat(
            model=ollama_model,
            messages=messages,
            options={"temperature": 0},
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        prediction = _parsed_prediction(str(extract_ollama_content(response)))
        prediction["latency_ms"] = elapsed_ms
        predictions.append(prediction)
    return predictions


def _summarize(expected: list[str], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [_prediction_label(prediction) for prediction in predictions]
    exact = sum(actual == wanted for actual, wanted in zip(labels, expected))
    numeric_errors = [
        abs(int(actual) - int(wanted))
        for actual, wanted in zip(labels, expected)
        if actual.isdigit() and wanted.isdigit()
    ]
    return {
        "exact": exact,
        "total": len(expected),
        "exact_accuracy": exact / len(expected),
        "mean_abs_error": sum(numeric_errors) / len(numeric_errors) if numeric_errors else None,
        "numeric_coverage": sum(label.isdigit() for label in labels),
        "invalid": labels.count("error"),
        "distribution": dict(sorted(Counter(labels).items())),
        "mean_latency_ms": sum(float(item["latency_ms"]) for item in predictions) / len(predictions),
    }


def _pair_summary(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    left_labels = [_prediction_label(item) for item in left]
    right_labels = [_prediction_label(item) for item in right]
    matches = sum(a == b for a, b in zip(left_labels, right_labels))
    return {
        "matches": matches,
        "total": len(left_labels),
        "agreement": matches / len(left_labels),
        "mismatch_indexes": [index for index, (a, b) in enumerate(zip(left_labels, right_labels)) if a != b],
    }


def _write_report(path: str, result: dict[str, Any]) -> None:
    lines = [
        "# Runtime Parity Report",
        "",
        f"Validation cases: {result['case_count']}",
        f"Merged model: `{result['merged_dir']}`",
        f"Ollama model: `{result['ollama_model']}`",
        "",
        "## Runtime agreement",
        "",
        "| Prompt profile | Matching predictions | Agreement |",
        "|---|---:|---:|",
    ]
    for profile in ("request-system", "package-default"):
        pair = result["runtime_agreement"][profile]
        lines.append(f"| {profile} | {pair['matches']}/{pair['total']} | {pair['agreement']:.1%} |")

    lines.extend([
        "",
        "## Accuracy by runtime and prompt profile",
        "",
        "| Runtime/profile | Exact | MAE | Distribution |",
        "|---|---:|---:|---|",
    ])
    for key in (
        "hf/request-system",
        "ollama/request-system",
        "hf/package-default",
        "ollama/package-default",
    ):
        summary = result["summaries"][key]
        mae = "N/A" if summary["mean_abs_error"] is None else f"{summary['mean_abs_error']:.3f}"
        lines.append(
            f"| {key} | {summary['exact']}/{summary['total']} "
            f"({summary['exact_accuracy']:.1%}) | {mae} | `{summary['distribution']}` |"
        )

    lines.extend([
        "",
        "## Prompt-profile sensitivity",
        "",
    ])
    for runtime in ("hf", "ollama"):
        pair = result["prompt_agreement"][runtime]
        lines.append(
            f"- {runtime}: {pair['matches']}/{pair['total']} predictions unchanged "
            f"({pair['agreement']:.1%})."
        )
    lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare merged HF and packaged Ollama validation predictions")
    parser.add_argument("--validation", required=True)
    parser.add_argument("--merged-dir", required=True)
    parser.add_argument("--modelfile", required=True)
    parser.add_argument("--ollama-model", required=True)
    parser.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--max-new-tokens", type=int, default=8)
    parser.add_argument("--hf-batch-size", type=int, default=4)
    args = parser.parse_args(argv)

    rows = _read_jsonl(args.validation)
    package_system = _read_modelfile_system(args.modelfile)
    expected = [_expected_label(row) for row in rows]
    predictions: dict[str, list[dict[str, Any]]] = {}

    for profile in ("request-system", "package-default"):
        print(f"[runtime-parity] hf profile={profile} cases={len(rows)}", flush=True)
        predictions[f"hf/{profile}"] = _run_hf(
            rows,
            args.merged_dir,
            profile,
            package_system,
            args.max_seq_length,
            args.max_new_tokens,
            args.hf_batch_size,
        )
        print(f"[runtime-parity] ollama profile={profile} cases={len(rows)}", flush=True)
        predictions[f"ollama/{profile}"] = _run_ollama(
            rows,
            args.ollama_host,
            args.ollama_model,
            profile,
            package_system,
        )

    result: dict[str, Any] = {
        "validation": args.validation,
        "case_count": len(rows),
        "merged_dir": args.merged_dir,
        "modelfile": args.modelfile,
        "ollama_model": args.ollama_model,
        "package_system": package_system,
        "summaries": {
            key: _summarize(expected, value) for key, value in predictions.items()
        },
        "runtime_agreement": {
            profile: _pair_summary(
                predictions[f"hf/{profile}"],
                predictions[f"ollama/{profile}"],
            )
            for profile in ("request-system", "package-default")
        },
        "prompt_agreement": {
            runtime: _pair_summary(
                predictions[f"{runtime}/request-system"],
                predictions[f"{runtime}/package-default"],
            )
            for runtime in ("hf", "ollama")
        },
        "cases": [],
    }
    for index, row in enumerate(rows):
        result["cases"].append({
            "index": index,
            "case_id": row.get("meta", {}).get("case_id", str(index)),
            "job_fingerprint": row.get("meta", {}).get("job_fingerprint", ""),
            "preference_key": row.get("meta", {}).get("preference_key", ""),
            "expected": expected[index],
            "predictions": {
                key: value[index] for key, value in predictions.items()
            },
        })

    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, "runtime-parity.json")
    report_path = os.path.join(args.output_dir, "runtime-parity.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    _write_report(report_path, result)
    print(f"[runtime-parity] json={json_path}")
    print(f"[runtime-parity] report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
