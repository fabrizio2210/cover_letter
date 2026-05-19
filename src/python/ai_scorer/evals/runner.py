"""
Runner: execute eval cases against a live Ollama model and return CaseResult list.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.python.ai_scorer.evals.metrics import CaseResult
from src.python.ai_scorer.evals.schema import EvalCase

try:
    from src.python.ai_scorer.ai_scorer import build_ollama_client, score_preference
except ImportError:
    from ai_scorer import build_ollama_client, score_preference  # type: ignore[no-redef]


def run_eval(
    cases: list,
    ollama_host: str,
    model_name: str,
    verbose: bool = False,
) -> list:
    """Score every EvalCase against `model_name` and return a list of CaseResult."""
    client = build_ollama_client(ollama_host)
    results: list = []

    for i, case in enumerate(cases, 1):
        if verbose:
            print(f"[runner] {i}/{len(cases)} {case.case_id} "
                  f"pref={case.preference_key!r} ...", end=" ", flush=True)

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

        actual_score: Optional[int] = None
        actual_available: Optional[bool] = None
        error: Optional[str] = None

        t0 = time.perf_counter()
        try:
            result = score_preference(
                ollama_client=client,
                model_name=model_name,
                test_mode=False,
                job_id=case.case_id,
                preference=preference,
                job_doc=job_doc,
                company_doc={},
                identity_doc={},
            )
            actual_score = result.get("score")
            actual_available = result.get("score_available", False)
        except Exception as exc:
            error = str(exc)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        if verbose:
            if error:
                print(f"ERROR: {error}")
            else:
                print(f"score={actual_score} available={actual_available}")

        results.append(
            CaseResult(
                case_id=case.case_id,
                model=model_name,
                expected_score=case.expected_score,
                expected_score_available=case.expected_score_available,
                actual_score=actual_score,
                actual_score_available=actual_available,
                error=error,
                latency_ms=latency_ms,
            )
        )

    return results
