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
python -m src.python.ai_scorer.training.cli label \
  --reuse-labels src/python/ai_scorer/training/data/proposed/labeled.json
python -m src.python.ai_scorer.training.cli export
```

To build a reusable, unlabeled pool of normalized job descriptions without
expanding preferences or changing the current split manifest:

```bash
python -m src.python.ai_scorer.training.cli extract \
  --mongo-uri 'mongodb://root:develop@127.0.0.1:27017/' \
  --jobs-only \
  --limit 500 \
  --job-pool-output src/python/ai_scorer/training/data/proposed/job-pool.json
```

This path makes no Gemini calls. It removes MongoDB IDs, deduplicates by the
normalized description fingerprint, excludes canonical golden fingerprints,
and uses deterministic length-stratified sampling. The pool retains full
normalized descriptions so preferences can be selected or changed before paid
labeling.

Mine a separate queue of likely-score-5 cases from that full-description pool:

```bash
python -m src.python.ai_scorer.training.cli mine-high-score-candidates
```

The selection reads only the ten maintained preferences and their guidance from
`data/training_preferences.seed.json`. It proposes at most 40 high-confidence
cases per preference, keeps strict evidence thresholds when the pool contains
fewer matches, limits a job to two selected preferences, and caps reuse of
identical evidence. Promotion fixtures are consulted only to exclude overlapping
job fingerprints; their preference keys, guidance, scores, and rationales do not
influence selection. Lexical score-5 estimates are recorded only in the review
report, while candidate labels remain null.

For the current 644-job pool this produces 353 cases across 263 jobs. Product
delivery, API design, and data pipelines have 25, 29, and 19 high-confidence
matches respectively; the other seven seed preferences each have 40. The
smaller groups are intentional—the miner does not weaken its evidence rules just
to spend the labeling budget.

Review these files before paying for labels:

- `src/python/ai_scorer/training/data/proposed/high-score-candidates.json`
- `src/python/ai_scorer/training/data/proposed/high-score-candidate-report.json`

When ready, label this queue independently so the current 500-label inventory
is not overwritten:

```bash
python -m src.python.ai_scorer.training.cli label \
  --input src/python/ai_scorer/training/data/proposed/high-score-candidates.json \
  --output src/python/ai_scorer/training/data/proposed/high-score-labeled.json \
  --allow-paid-calls
```

Do not export the new queue by itself. After labeling, inspect its actual score
distribution, then merge the accepted cases into the maintained inventory and
assign their full-description fingerprints to train/validation.

Reconcile the preserved paid-label inventory against the complete pool without
changing any training artifact:

```bash
python -m src.python.ai_scorer.training.cli reconcile-job-pool
```

The command validates and hashes its inputs, recomputes pool fingerprints,
rejects golden overlap, and writes both JSON and Markdown reports under
`src/python/ai_scorer/training/data/proposed/`. A mapping is proposed only when
all stored snippets occur in exactly one full description and both normalized
title and location agree. Ambiguous and proposed matches remain unchanged until
separately reviewed.

After reviewing the proposed mappings, apply exactly that unchanged report:

```bash
python -m src.python.ai_scorer.training.cli apply-job-pool-reconciliation
```

The apply command refuses stale reports, validates candidate/label alignment,
stages all three source artifacts before replacement, preserves existing split
assignments, records newly recovered groups without reshuffling, and writes an
apply receipt. A mixed legacy/full manifest is accepted only when every full
fingerprint is backed by one of these reviewed mappings.

Default outputs:
- `src/python/ai_scorer/training/data/proposed/candidates.json`
- `src/python/ai_scorer/training/data/proposed/labeled.json`
- `src/python/ai_scorer/training/data/proposed/split-manifest.json`
- `src/python/ai_scorer/training/data/export/{train,val}.jsonl`

Gemini labeling requires `GEMINI_TOKEN` and the explicit
`--allow-paid-calls` flag. Exact reusable labels are copied before any paid
calls are considered, and the CLI reports the paid case count before stopping.
Labels already present in the input—including N/A labels—are preserved by
default and take precedence over `--reuse-labels`. Relabeling is possible only
with the additional explicit `--overwrite-labels` flag; in that mode all input
and reusable labels are ignored and every case counts as a paid call.

Jobs are identified by versioned job-description fingerprints rather than
database IDs. Future extraction excludes golden fingerprints and creates a
deterministic train/validation split before expanding preferences. Export only
consumes that persisted assignment. All preference cases for the same
fingerprint stay in one split. The canonical 53-case scorer evaluation remains
the separate final promotion gate; there is no internal test split.

The current paid dataset predates full-description fingerprints. Its 500 labels
are preserved with a `legacy-partial` fingerprint computed from the union of
stored snippets. Confirmed golden overlaps and ambiguous matches remain in the
label inventory but are withheld from training exports.

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
non-empty train/validation splits, duplicate `case_id` values, persisted split
assignments, stale golden metadata, and fingerprint leakage across train,
validation, and promotion exclusions.

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

Training defaults to `--sampling-mode job-preference-balanced`. Before model
loading, exact duplicate inputs are collapsed in the derived training view. If
their labels conflict, a strict majority is used; ties are withheld and
reported. The source JSONL and paid-label inventory are never changed. Each
epoch then selects one example per `(job fingerprint, preference key)` and
rotates through alternative prompts deterministically across epochs. This gives
every job equal per-preference exposure while still using oversized groups over
time.

Audit the current schedule without loading a model:

```bash
python -m src.python.ai_scorer.training.cli balance-audit
```

Use `--sampling-mode all` only for an intentional unbalanced baseline. The run
manifest and `training_balance.json` record the sampling mode, conflict
resolution, effective records per epoch, and rotation coverage.

Future paid labeling should add new full-description job fingerprints and may
introduce preferences aligned with production and golden coverage, including
remote work, coding, and backend/infrastructure. Labeling remains a separate,
explicit paid step; balance auditing and training make no external API calls.

For CPU training, `--cpu-threads` explicitly configures PyTorch intra-op
parallelism and the matching `OMP_NUM_THREADS` and `MKL_NUM_THREADS` values.
It also defaults OpenMP to `OMP_DYNAMIC=FALSE`, `OMP_PROC_BIND=spread`, and
`OMP_PLACES=threads`. Use `taskset` separately when the process should be pinned
to specific logical CPUs:

```bash
taskset -c 0-21 python3 -m src.python.ai_scorer.training.cli train \
--dataset-profile keep-system \
--loss-mode response-only \
--cpu-threads 22 \
--cpu-interop-threads 1 \
--num-train-epochs 12 \
--max-steps -1 \
--run-id qwen25-response-only-12epochs
```

The startup output and `run_manifest.json` report the effective PyTorch thread
counts and process CPU affinity.

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
