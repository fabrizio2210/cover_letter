from __future__ import annotations

import argparse
import os
import subprocess

from src.python.ai_scorer.training.fine_tune_manifest import now_epoch, write_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run scorer eval as promotion gate")
    parser.add_argument("--candidate-model", required=True)
    parser.add_argument("--run-dir", default="", help="Optional run directory to store gate manifest")
    parser.add_argument("--eval-script", default="scripts/eval-scorer.sh")
    parser.add_argument("--fixtures", default="")
    args = parser.parse_args(argv)

    cmd = ["bash", args.eval_script, args.candidate_model]
    if args.fixtures:
        cmd.append(args.fixtures)

    completed = subprocess.run(cmd, check=False)

    manifest = {
        "candidate_model": args.candidate_model,
        "eval_script": args.eval_script,
        "fixtures": args.fixtures,
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "completed_at_epoch": now_epoch(),
    }

    if args.run_dir:
        os.makedirs(args.run_dir, exist_ok=True)
        manifest_path = os.path.join(args.run_dir, "eval_gate_manifest.json")
        write_manifest(manifest_path, manifest)
        print(f"[training.eval-gate] manifest={manifest_path}")

    if completed.returncode == 0:
        print("[training.eval-gate] gate=PASSED")
        return 0

    print("[training.eval-gate] gate=FAILED")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
