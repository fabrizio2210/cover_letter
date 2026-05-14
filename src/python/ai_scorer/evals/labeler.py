"""
Labeler: run the scorer model against candidate fixtures and propose labels.

For each unlabeled case the model is called and its response is written as the
proposed expected_score / expected_score_available.  The rationale field records
the raw model response for human review.

Usage (from repo root):
    python -m src.python.ai_scorer.evals.labeler \\
        --ollama-host http://localhost:11434 \\
        --model qwen2.5:1.5b \\
        --input  src/python/ai_scorer/evals/data/proposed/candidates.json \\
        --output src/python/ai_scorer/evals/data/proposed/labeled.json

After running, review the output file and edit/correct any labels before
promoting to data/canonical/.
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.python.ai_scorer.evals.schema import EvalCase, dump_fixtures, load_fixtures

try:
    from src.python.ai_scorer.ai_scorer import (
        build_ollama_client,
        extract_ollama_content,
        parse_ollama_response,
        score_preference,
    )
except ImportError:
    from ai_scorer import (  # type: ignore[no-redef]
        build_ollama_client,
        extract_ollama_content,
        parse_ollama_response,
        score_preference,
    )


def _label_case(case: EvalCase, ollama_client, model_name: str) -> EvalCase:
    """Run the model for one case and fill in proposed labels."""
    job_doc = {
        "title": case.title,
        "description": case.description,
        "location": case.location,
    }
    preference = {
        "key": case.preference_key,
        "guidance": case.preference_guidance,
        "weight": 1.0,
        "enabled": True,
    }
    try:
        result = score_preference(
            ollama_client=ollama_client,
            model_name=model_name,
            test_mode=False,
            job_id=case.case_id,
            preference=preference,
            job_doc=job_doc,
            company_doc={},
            identity_doc={},
        )
        proposed_score = result.get("score")
        proposed_available = result.get("score_available", False)
        rationale = f"[model={model_name}] score={proposed_score} available={proposed_available}"
    except Exception as exc:
        proposed_score = None
        proposed_available = False
        rationale = f"[model={model_name}] ERROR: {exc}"

    # N/A case: score_available=False means expected_score must be None
    expected_score = proposed_score if proposed_available else None

    return EvalCase(
        case_id=case.case_id,
        title=case.title,
        description=case.description,
        location=case.location,
        preference_key=case.preference_key,
        preference_guidance=case.preference_guidance,
        expected_score=expected_score,
        expected_score_available=proposed_available,
        rationale=rationale,
        tags=case.tags,
        schema_version=case.schema_version,
        provenance=case.provenance,
    )


def label_candidates(
    input_path: str,
    output_path: str,
    ollama_host: str,
    model_name: str,
) -> None:
    cases = load_fixtures(input_path)
    unlabeled = [c for c in cases if c.expected_score_available is None]
    already_labeled = [c for c in cases if c.expected_score_available is not None]

    print(f"[labeler] {len(cases)} total cases: "
          f"{len(unlabeled)} unlabeled, {len(already_labeled)} already labeled")

    if not unlabeled:
        print("[labeler] Nothing to label.")
        return

    ollama_client = build_ollama_client(ollama_host)
    labeled: list = list(already_labeled)

    for i, case in enumerate(unlabeled, 1):
        print(f"[labeler] {i}/{len(unlabeled)} {case.case_id} "
              f"pref={case.preference_key!r} ...", end=" ", flush=True)
        result = _label_case(case, ollama_client, model_name)
        labeled.append(result)
        print(f"score={result.expected_score} available={result.expected_score_available}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    dump_fixtures(labeled, output_path)
    print(f"[labeler] Done → {output_path}")
    print("[labeler] Review the output, correct any labels, then copy to data/canonical/.")


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(
        description="Propose labels for candidate eval fixtures using a live Ollama model"
    )
    parser.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        help="Ollama base URL (default: OLLAMA_HOST env or localhost:11434)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
        help="Ollama model name (default: OLLAMA_MODEL env or qwen2.5:1.5b)",
    )
    parser.add_argument(
        "--input",
        default="src/python/ai_scorer/evals/data/proposed/candidates.json",
        help="Input candidate fixture file",
    )
    parser.add_argument(
        "--output",
        default="src/python/ai_scorer/evals/data/proposed/labeled.json",
        help="Output proposed-label fixture file",
    )
    args = parser.parse_args(argv)

    label_candidates(
        input_path=args.input,
        output_path=args.output,
        ollama_host=args.ollama_host,
        model_name=args.model,
    )


if __name__ == "__main__":
    main()
