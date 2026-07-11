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

## Fine-tuning and packaging workflow

The training package includes an end-to-end fine-tuning workflow for qwen2.5:1.5b
with a preferred CUDA path (`unsloth + trl`) and a CPU fallback path
(`transformers + peft`). Runtime path selection is automatic.

Decision contract:
- `src/python/ai_scorer/training/fine_tune.contract.json`

Optional training dependencies:
- `src/python/ai_scorer/training/requirements-training.txt`

### 1) Dataset preflight

Validate exported JSONL splits before training:

```bash
python3 -m src.python.ai_scorer.training.cli preflight --dataset-profile keep-system
python3 -m src.python.ai_scorer.training.cli preflight --dataset-profile no-system
```

Checks include role order, assistant labels (`0..5` or `N/A`), empty content,
split presence/counts, and duplicate `case_id` values.

### 2) Runtime detection

```bash
python3 -m src.python.ai_scorer.training.cli detect-runtime
```

### 3) Fine-tuning launch

CPU-safe smoke run:

```bash
python3 -m src.python.ai_scorer.training.cli train \
  --dataset-profile keep-system \
  --smoke-run
```

Typical run:

```bash
python3 -m src.python.ai_scorer.training.cli train \
  --dataset-profile keep-system \
  --max-steps 400 \
  --gradient-accumulation-steps 16 \
  --run-id qwen25-keep-system-r1
```

Artifacts are written under:
- `src/python/ai_scorer/training/artifacts/runs/<run-id>/`

Each run writes `run_manifest.json` with config hash inputs, dataset hash, git
SHA, runtime selection, and elapsed time.

### 4) Merge adapters into full HF weights

```bash
python3 -m src.python.ai_scorer.training.cli merge \
  --run-dir src/python/ai_scorer/training/artifacts/runs/<run-id>
```

### 5) Package to GGUF and Ollama

```bash
python3 -m src.python.ai_scorer.training.cli package \
  --run-dir src/python/ai_scorer/training/artifacts/runs/<run-id> \
  --convert-script /path/to/llama.cpp/convert_hf_to_gguf.py \
  --ollama-tag ai-scorer-qwen25:<run-id>
```

### 6) Promotion gate (existing scorer eval)

```bash
python3 -m src.python.ai_scorer.training.cli eval-gate \
  --candidate-model ai-scorer-qwen25:<run-id> \
  --run-dir src/python/ai_scorer/training/artifacts/runs/<run-id>
```

This executes `scripts/eval-scorer.sh` and persists gate pass/fail metadata.
