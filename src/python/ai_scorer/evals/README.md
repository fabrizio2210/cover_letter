# AI Scorer Evals

This folder contains manual golden-eval tooling for scorer prompt/model regression checks.

## Files

- `core.py`: fixture validation, metric computation, and baseline-vs-candidate threshold checks.
- `extract_goldens.py`: exports proposed golden cases from MongoDB with default redaction.
- `run_eval.py`: runs live model evaluation for baseline and candidate models.
- `data/canonical_cases.sample.json`: sample canonical fixture file format.

## 1) Extract proposed golden cases

Example preferences file:

```json
[
  {"key": "remote", "guidance": "Prefers fully remote roles"},
  {"key": "backend", "guidance": "Prefers backend engineering work"}
]
```

Run extraction:

```bash
PYTHONPATH=. python3 src/python/ai_scorer/evals/extract_goldens.py \
  --mongo-uri 'mongodb://root:develop@localhost:27017/admin' \
  --db-name cover_letter_global \
  --limit 30 \
  --preferences-json src/python/ai_scorer/evals/data/preferences.sample.json \
  --out src/python/ai_scorer/evals/data/proposed_cases.json
```

## 2) Review and promote to canonical fixtures

- Copy reviewed cases into a canonical file (same schema as sample fixture).
- Ensure each case has `expected.score_available` and `expected.score` values validated for scoring.

## 3) Run baseline vs candidate eval

```bash
PYTHONPATH=. python3 src/python/ai_scorer/evals/run_eval.py \
  --fixtures src/python/ai_scorer/evals/data/canonical_cases.sample.json \
  --baseline-model qwen2.5:1.5b \
  --candidate-model qwen2.5:1.5b \
  --ollama-host http://localhost:11434 \
  --out-dir /tmp/ai_scorer_eval_run
```

Artifacts:

- `/tmp/ai_scorer_eval_run/summary.json`
- `/tmp/ai_scorer_eval_run/case_diffs.json`
- `/tmp/ai_scorer_eval_run/report.md`

## Threshold defaults

- `exact_accuracy_drop <= 0.03`
- `na_f1_drop <= 0.05`
- `mean_abs_error_increase <= 0.20`

These can be overridden via CLI flags in `run_eval.py`.
