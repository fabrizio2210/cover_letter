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
python -m src.python.ai_scorer.training.cli export --val-ratio 0.1
```

Default outputs:
- `src/python/ai_scorer/training/data/proposed/candidates.json`
- `src/python/ai_scorer/training/data/proposed/labeled.json`
- `src/python/ai_scorer/training/data/export/{train,val}.jsonl`

Gemini labeling requires `GEMINI_TOKEN` in environment.

Dataset export creates a 90/10 train/validation split by `source_job_id` by
default. All preference cases for the same source job stay in one split, so
validation measures performance on jobs that were not present during training.
The validation ratio must be greater than zero and less than one. The canonical
53-case scorer evaluation remains the separate final promotion gate; there is
no internal test split.

## Fine-tuning and packaging workflow

The training package includes an end-to-end fine-tuning workflow for qwen2.5:1.5b
with a preferred CUDA path (`unsloth + trl`) and a CPU fallback path
(`transformers + peft`). Runtime path selection is automatic.

Training examples are always formatted with Qwen's native chat template. The
`--loss-mode` option selects which non-padding tokens contribute to the loss:

- `response-only` (experimental default) masks the system and user prompt and
  supervises only the assistant score and Qwen end-of-turn token.
- `chat-full` supervises every retained chat token and provides a native-chat
  full-sequence baseline for controlled comparisons.

Both modes reserve enough space for the complete assistant score and
end-of-turn target before truncating over-length context from the left. Padding
is always excluded from the loss.

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
non-empty train/validation splits, duplicate `case_id` values, and
`source_job_id` leakage across splits.

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
  --loss-mode response-only \
  --max-steps 400 \
  --gradient-accumulation-steps 16 \
  --run-id qwen25-keep-system-r1
```

Artifacts are written under:
- `src/python/ai_scorer/training/artifacts/runs/<run-id>/`

Each run writes `run_manifest.json` with config hash inputs, dataset hash, loss
mode, git SHA, runtime selection, and elapsed time.

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
