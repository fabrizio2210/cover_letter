from __future__ import annotations

import argparse
import os

from src.python.ai_scorer.training.schema import TrainingCase, dump_cases, load_cases, validate_cases

try:
    from src.python.ai_scorer.ai_scorer import parse_ollama_response
except ImportError:
    from ai_scorer import parse_ollama_response  # type: ignore[no-redef]


def _build_gemini_model(model_name: str):
    api_token = os.environ.get("GEMINI_TOKEN")
    if not api_token:
        raise RuntimeError("Environment variable GEMINI_TOKEN is required for Gemini labeling")

    import google.generativeai as genai

    genai.configure(api_key=api_token)
    return genai.GenerativeModel(model_name)


def _label_case(model_name: str, case: TrainingCase) -> tuple[int | None, bool]:
    import google.generativeai as genai

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


def label_cases(cases: list[TrainingCase], model_name: str) -> list[TrainingCase]:
    _build_gemini_model(model_name)
    output: list[TrainingCase] = []

    for idx, case in enumerate(cases, start=1):
        score, available = _label_case(model_name, case)
        labeled = TrainingCase(
            case_id=case.case_id,
            source_job_id=case.source_job_id,
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
        output.append(labeled)
        print(
            f"[training.label] {idx}/{len(cases)} case={case.case_id} "
            f"score={'N/A' if not available else score}"
        )

    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Label training cases with Gemini")
    parser.add_argument("--input", default="src/python/ai_scorer/training/data/proposed/candidates.json")
    parser.add_argument("--output", default="src/python/ai_scorer/training/data/proposed/labeled.json")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    args = parser.parse_args(argv)

    cases = load_cases(args.input)
    errors = validate_cases(cases)
    if errors:
        print("[training.label] ERROR: input validation failed")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(2)

    labeled = label_cases(cases, model_name=args.model)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    dump_cases(labeled, args.output)
    print(f"[training.label] done -> {args.output}")


if __name__ == "__main__":
    main()
