from __future__ import annotations

import argparse
import os

from src.python.ai_scorer.training.schema import TrainingCase, dump_cases, load_cases, validate_cases


def _build_gemini_model(model_name: str):
    api_token = os.environ.get("GEMINI_TOKEN")
    if not api_token:
        raise RuntimeError("Environment variable GEMINI_TOKEN is required for Gemini labeling")

    import google.generativeai as genai

    genai.configure(api_key=api_token)
    return genai.GenerativeModel(model_name)


def _label_case(model_name: str, case: TrainingCase) -> tuple[int | None, bool]:
    import google.generativeai as genai
    from src.python.ai_scorer.ai_scorer import parse_ollama_response

    configured = genai.GenerativeModel(
        model_name,
        system_instruction=case.system_prompt,
    )
    response = configured.generate_content(case.user_prompt)
    content = str(getattr(response, "text", "") or "").strip()

    score, score_available, _ = parse_ollama_response(content)
    if score_available is False:
        return None, False
    if score is None:
        raise ValueError(f"Unable to parse Gemini label for case {case.case_id}: {content!r}")
    if score < 0 or score > 5:
        raise ValueError(f"Gemini score out of range for case {case.case_id}: {score}")
    return score, True


def _reuse_key(case: TrainingCase) -> tuple[str, ...]:
    return (
        case.job_fingerprint,
        case.fingerprint_basis,
        case.preference_key,
        case.preference_guidance,
        case.system_prompt,
        case.user_prompt,
    )


def _with_label(case: TrainingCase, score: int | None, available: bool) -> TrainingCase:
    return TrainingCase(
        case_id=case.case_id,
        job_fingerprint=case.job_fingerprint,
        fingerprint_basis=case.fingerprint_basis,
        title=case.title,
        location=case.location,
        preference_key=case.preference_key,
        preference_guidance=case.preference_guidance,
        relevant_snippets=case.relevant_snippets,
        system_prompt=case.system_prompt,
        user_prompt=case.user_prompt,
        label_score=score,
        label_available=available,
        schema_version=case.schema_version,
    )


def label_cases(
    cases: list[TrainingCase],
    model_name: str,
    *,
    reusable_cases: list[TrainingCase] | None = None,
    allow_paid_calls: bool = False,
    overwrite_labels: bool = False,
) -> list[TrainingCase]:
    reusable = (
        {}
        if overwrite_labels
        else {
            _reuse_key(case): case
            for case in (reusable_cases or [])
            if case.label_available is not None
        }
    )
    preserved_count = 0 if overwrite_labels else sum(
        case.label_available is not None for case in cases
    )
    reusable_count = 0 if overwrite_labels else sum(
        case.label_available is None and _reuse_key(case) in reusable
        for case in cases
    )
    paid_count = len(cases) - preserved_count - reusable_count
    print(
        f"[training.label] preserved={preserved_count} reusable={reusable_count} "
        f"paid_required={paid_count} overwrite_labels={str(overwrite_labels).lower()}"
    )
    if paid_count and not allow_paid_calls:
        raise RuntimeError(
            f"{paid_count} cases require Gemini; rerun with --allow-paid-calls after reviewing the counts"
        )
    if paid_count:
        _build_gemini_model(model_name)

    output: list[TrainingCase] = []

    for idx, case in enumerate(cases, start=1):
        reused = reusable.get(_reuse_key(case)) if case.label_available is None else None
        if not overwrite_labels and case.label_available is not None:
            score, available = case.label_score, bool(case.label_available)
            source = "preserved"
        elif reused is not None:
            score, available = reused.label_score, bool(reused.label_available)
            source = "reused"
        else:
            score, available = _label_case(model_name, case)
            source = "gemini"
        labeled = _with_label(case, score, available)
        output.append(labeled)
        print(
            f"[training.label] {idx}/{len(cases)} case={case.case_id} "
            f"score={'N/A' if not available else score} source={source}"
        )

    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Label training cases with Gemini")
    parser.add_argument("--input", default="src/python/ai_scorer/training/data/proposed/candidates.json")
    parser.add_argument("--output", default="src/python/ai_scorer/training/data/proposed/labeled.json")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    parser.add_argument("--reuse-labels", default="")
    parser.add_argument("--allow-paid-calls", action="store_true")
    parser.add_argument(
        "--overwrite-labels",
        action="store_true",
        help="Relabel every input case with Gemini, ignoring input and reusable labels",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.input)
    errors = validate_cases(cases)
    if errors:
        print("[training.label] ERROR: input validation failed")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(2)

    reusable_cases = load_cases(args.reuse_labels) if args.reuse_labels else []
    labeled = label_cases(
        cases,
        model_name=args.model,
        reusable_cases=reusable_cases,
        allow_paid_calls=args.allow_paid_calls,
        overwrite_labels=args.overwrite_labels,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    dump_cases(labeled, args.output)
    print(f"[training.label] done -> {args.output}")


if __name__ == "__main__":
    main()
