from __future__ import annotations

import argparse
import json
import os

from src.python.ai_scorer.training.fine_tune_manifest import now_epoch, write_manifest
from src.python.ai_scorer.training.fine_tune_models import (
    MODEL_PROFILE_NAMES,
    ModelProfile,
    load_causal_lm,
    load_tokenizer,
    resolve_run_model_profile,
)


def _merge_adapter(model_profile: ModelProfile, adapter_dir: str, merged_dir: str) -> None:
    try:
        from peft import PeftModel  # type: ignore
    except Exception as exc:
        raise RuntimeError("Merge requires transformers and peft dependencies") from exc

    tokenizer = load_tokenizer(model_profile)
    base_model = load_causal_lm(model_profile)
    model = PeftModel.from_pretrained(base_model, adapter_dir)
    merged = model.merge_and_unload()

    os.makedirs(merged_dir, exist_ok=True)
    merged.save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)


def resolve_adapter_dir(run_dir: str, override: str = "") -> str:
    if override:
        return override

    validation_path = os.path.join(run_dir, "checkpoint_validation.json")
    if not os.path.isfile(validation_path):
        return run_dir
    with open(validation_path, "r", encoding="utf-8") as handle:
        validation = json.load(handle)
    try:
        selected_step = int(validation["selected_checkpoint_step"])
        recorded_dir = str(validation["selected_checkpoint_dir"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid checkpoint selection manifest: {validation_path}"
        ) from exc

    expected_dir = os.path.join(run_dir, f"checkpoint-{selected_step}")
    if os.path.realpath(recorded_dir) != os.path.realpath(expected_dir):
        raise ValueError(
            "Selected checkpoint path does not match its run and step: "
            f"recorded={recorded_dir} expected={expected_dir}"
        )
    if not os.path.isdir(expected_dir):
        raise ValueError(f"Selected checkpoint directory does not exist: {expected_dir}")
    return expected_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge LoRA adapters into full HF weights")
    parser.add_argument("--model-profile", choices=MODEL_PROFILE_NAMES, default="")
    parser.add_argument("--base-model-hf", default="")
    parser.add_argument("--base-model-revision", default="")
    parser.add_argument("--run-dir", required=True, help="Training run directory")
    parser.add_argument("--adapter-dir", default="", help="Adapter directory; defaults to --run-dir")
    parser.add_argument("--merged-dir", default="", help="Output merged directory; defaults to <run-dir>/merged-hf")
    args = parser.parse_args(argv)

    try:
        adapter_dir = resolve_adapter_dir(args.run_dir, args.adapter_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    merged_dir = args.merged_dir or os.path.join(args.run_dir, "merged-hf")
    if not os.path.isdir(adapter_dir):
        raise SystemExit(f"Adapter directory does not exist: {adapter_dir}")

    model_profile = resolve_run_model_profile(
        args.run_dir,
        profile_name=args.model_profile,
        hf_id=args.base_model_hf,
        revision=args.base_model_revision,
    )
    _merge_adapter(model_profile, adapter_dir, merged_dir)

    manifest_path = os.path.join(args.run_dir, "merge_manifest.json")
    write_manifest(
        manifest_path,
        {
            "model_profile": model_profile.name,
            "base_model_hf": model_profile.hf_id,
            "base_model_revision": model_profile.revision,
            "torch_dtype": model_profile.torch_dtype,
            "loader": model_profile.loader,
            "adapter_dir": adapter_dir,
            "merged_dir": merged_dir,
            "completed_at_epoch": now_epoch(),
        },
    )

    print(f"[training.merge] merged_dir={merged_dir}")
    print(f"[training.merge] manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
