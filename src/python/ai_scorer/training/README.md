# Training Dataset Runtime

This package creates supervised fine-tuning datasets for `ai_scorer`.

## Key constraints

- Prompt building reuses scorer runtime logic from `src/python/ai_scorer/ai_scorer.py`.
- Snippet extraction reuses scorer runtime behavior (`normalize_description_markdown`,
  `retrieve_relevant_snippets`) so training records reflect real inference context.
- The system prompt is duplicated in every exported case.
- The default preference seed set contains 10 synthetic preferences with `key` and
  `guidance` fields.

## CLI

```bash
python -m src.python.ai_scorer.training.cli generate-preferences
python -m src.python.ai_scorer.training.cli extract --limit 50
python -m src.python.ai_scorer.training.cli label --model gemini-3.5-flash
python -m src.python.ai_scorer.training.cli export
```

Default outputs:
- `src/python/ai_scorer/training/data/proposed/candidates.json`
- `src/python/ai_scorer/training/data/proposed/labeled.json`
- `src/python/ai_scorer/training/data/export/{train,val,test}.jsonl`

Gemini labeling requires `GEMINI_TOKEN` in environment.
