from __future__ import annotations

import argparse
import os

from src.python.ai_scorer.training.fine_tune_manifest import now_epoch, write_manifest


def _merge_adapter(base_model_hf: str, adapter_dir: str, merged_dir: str) -> None:
    try:
        from peft import PeftModel  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
    except Exception as exc:
        raise RuntimeError("Merge requires transformers and peft dependencies") from exc

    tokenizer = AutoTokenizer.from_pretrained(base_model_hf, use_fast=True)
    base_model = AutoModelForCausalLM.from_pretrained(base_model_hf)
    model = PeftModel.from_pretrained(base_model, adapter_dir)
    merged = model.merge_and_unload()

    os.makedirs(merged_dir, exist_ok=True)
    merged.save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge LoRA adapters into full HF weights")
    parser.add_argument("--base-model-hf", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--run-dir", required=True, help="Training run directory")
    parser.add_argument("--adapter-dir", default="", help="Adapter directory; defaults to --run-dir")
    parser.add_argument("--merged-dir", default="", help="Output merged directory; defaults to <run-dir>/merged-hf")
    args = parser.parse_args(argv)

    adapter_dir = args.adapter_dir or args.run_dir
    merged_dir = args.merged_dir or os.path.join(args.run_dir, "merged-hf")
    if not os.path.isdir(adapter_dir):
        raise SystemExit(f"Adapter directory does not exist: {adapter_dir}")

    _merge_adapter(args.base_model_hf, adapter_dir, merged_dir)

    manifest_path = os.path.join(args.run_dir, "merge_manifest.json")
    write_manifest(
        manifest_path,
        {
            "base_model_hf": args.base_model_hf,
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
