# AI Scorer Evals

This folder contains manual golden-eval tooling for scorer prompt/model regression checks.

## Files

- `cli.py`: unified entrypoint for `extract`, `label`, and `eval`.
- `extractor.py`: canonical candidate fixture extractor used by `cli extract`.
- `schema.py`: canonical fixture schema, validation, and serialization.
- `redaction.py`: deterministic redaction helpers used during extraction.
- `runner.py`: live model execution used by `cli eval`.
- `core.py`: validation and regression-threshold helpers used by the older direct eval path.
- `data/canonical/v1.json`: checked-in canonical fixture file.
- `data/canonical_cases.sample.json`: minimal bare-array fixture example.

## 1) Extract candidate cases

Example preferences file:

```json
[
  {"key": "remote", "guidance": "Prefers fully remote roles"},
  {"key": "backend", "guidance": "Prefers backend engineering work"}
]
```

Run extraction with the canonical CLI:

```bash
PYTHONPATH=. python3 -m src.python.ai_scorer.evals.cli extract \
  --mongo-uri 'mongodb://root:develop@localhost:27017/admin' \
  --global-db cover_letter_global \
  --limit 30 \
  --preferences src/python/ai_scorer/evals/data/preferences.sample.json \
  --output src/python/ai_scorer/evals/data/proposed/candidates.json
```

The extractor writes an unlabeled candidate file. It uses the same case fields as the canonical schema, but `expected_score_available` and `expected_score` are left unset until review.

## 2) Review and promote to canonical fixtures

Generate first-pass labels:

```bash
PYTHONPATH=. python3 -m src.python.ai_scorer.evals.cli label \
  --ollama-host http://localhost:11434 \
  --model qwen2.5:1.5b \
  --input src/python/ai_scorer/evals/data/proposed/candidates.json \
  --output src/python/ai_scorer/evals/data/proposed/labeled.json
```

- Review the labeled output and correct it manually.
- Copy approved cases into `data/canonical/v1.json` using the canonical `{meta, cases}` structure.
- Ensure each canonical case has `expected.score_available` and `expected.score` set before it is used for eval.

## 3) Run eval against the canonical fixture set

```bash
PYTHONPATH=. python3 -m src.python.ai_scorer.evals.cli eval \
  --fixtures src/python/ai_scorer/evals/data/canonical/v1.json \
  --candidate qwen2.5:1.5b \
  --ollama-host http://localhost:11434 \
  --output-dir /tmp/ai_scorer_eval_run
```

Artifacts:

- `/tmp/ai_scorer_eval_run/summary.json`
- `/tmp/ai_scorer_eval_run/case_diffs.json`
- `/tmp/ai_scorer_eval_run/report.md`

## Threshold defaults

- `exact_accuracy_drop <= 0.03`
- `na_f1_drop <= 0.05`
- `mean_abs_error_increase <= 0.20`

These can be overridden via CLI flags in `cli.py eval`.

## 4) Optional: Run eval without system prompt

By default, evaluations include the system prompt in all Ollama requests. To compare model behavior without the system prompt (e.g., for ablation studies), use the `EVAL_WITH_SYSTEM_PROMPT` environment variable:

```bash
# Eval without system prompt
EVAL_WITH_SYSTEM_PROMPT=false PYTHONPATH=. python3 -m src.python.ai_scorer.evals.cli eval \
  --fixtures src/python/ai_scorer/evals/data/canonical/v1.json \
  --candidate qwen2.5:1.5b \
  --ollama-host http://localhost:11434 \
  --output-dir /tmp/ai_scorer_eval_run_no_system
```

Via `eval-scorer.sh`:

```bash
# Eval without system prompt
EVAL_WITH_SYSTEM_PROMPT=false bash scripts/eval-scorer.sh qwen2.5:1.5b
```

Via training CLI `eval-gate`:

```bash
# Run eval gate without system prompt
python3 -m src.python.ai_scorer.training.cli eval-gate \
  --candidate-model qwen2.5:1.5b \
  --without-system-prompt
```

**Environment variable:** `EVAL_WITH_SYSTEM_PROMPT` (default: `true`)
- `true` or `1` or `yes` — Include system prompt (default)
- `false` or `0` or `no` — Exclude system prompt
