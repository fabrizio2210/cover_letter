# Training TODO

The current training workflow is directionally sound, but the following issues
should be addressed before relying on a fine-tuned model for promotion.

## Required actions

1. Split by `source_job_id` before expanding jobs into preferences, and exclude
   every job ID used by the promotion fixtures from the training and validation
   pool.

2. **Implemented, pending controlled validation:** use Qwen's native chat
   template, EOS/end-of-turn supervision, answer-preserving truncation, and
   padding masking. Response-only loss remains experimental; the training CLI
   also provides a native-chat full-sequence baseline so both objectives can be
   compared with identical data and training settings.

3. Implement a genuinely branched QLoRA training path, or rename and document
   the current implementation honestly as CPU LoRA.

4. Add task-level validation and promotion-test generation metrics, including
   exact score accuracy, within-one accuracy, mean absolute error, per-label
   precision/recall, macro-F1, N/A precision/recall/F1, invalid response rate,
   numeric-answer coverage, and predicted score distribution. Report these
   metrics by preference and by seen/unseen job. The canonical 53-case
   promotion set should remain separate from the internal validation set.

5. Expand and balance the dataset, record teacher-model provenance, set
   deterministic labeling parameters, and add retry and incremental checkpoint
   support to the labeling workflow.

6. Fix packaging paths and enforce consistent prompt profiles across dataset
   export, training, GGUF/Ollama packaging, and runtime inference.

## Promotion-fixture contamination

The canonical promotion set is not currently independent from the training
pool. Of its 53 cases, 12 cases share job descriptions with the labeled
training data. These cases span seven distinct `source_job_id` values.

Although the preferences and expected labels are not necessarily identical,
the model can still see the same underlying job descriptions during training.
This can inflate promotion results and makes the 53 cases unsuitable as a
strictly untouched final gate.

Before regenerating train and validation splits:

1. Collect every `provenance.source_job_id` from the canonical promotion
   fixtures.
2. Remove matching jobs from the candidate training pool before expanding them
   into preference cases.
3. Split the remaining jobs into train and validation sets by `source_job_id`.
4. Add a preflight check that fails if any source job appears in more than one
   of train, validation, or promotion.

With the current data, excluding the seven overlapping jobs would remove 70 of
the 500 labeled cases because each job was expanded across ten preferences.

## Recommended train/validation design

The generated training artifacts should contain only train and validation
sets. The canonical 53 cases serve as the external promotion test and must not
be used for training, validation, checkpoint selection, early stopping, or
label generation.

1. Exclude promotion-fixture jobs first.
2. Split approximately 85-90% of the remaining unique jobs into training and
   10-15% into validation.
3. Expand each partition into preference examples only after the job-level
   split.
4. Store the job IDs and dataset hash in the run manifest.
5. Fail preflight validation if train, validation, and promotion contain any
   overlapping `source_job_id` values.

Exact row counts such as 450/50 are less important than keeping all examples
derived from a job in one partition.

## Dataset distribution and coverage

The response-only 12-epoch run used a training distribution that differs
substantially from the promotion set:

| Score | Training count | Training share | Promotion count | Promotion share |
| --- | ---: | ---: | ---: | ---: |
| 0 | 185 | 41.1% | 10 | 18.9% |
| 1 | 65 | 14.4% | 0 | 0.0% |
| 2 | 75 | 16.7% | 6 | 11.3% |
| 3 | 61 | 13.6% | 8 | 15.1% |
| 4 | 30 | 6.7% | 10 | 18.9% |
| 5 | 7 | 1.6% | 16 | 30.2% |
| N/A | 27 | 6.0% | 3 | 5.7% |

This makes avoiding score 5 a rational training outcome even though score 5
is the most common promotion label. Before further long runs:

1. Add diverse, independently sourced examples for scores 4 and 5.
2. Add hard boundary examples for 2 versus 3 and 3 versus 4.
3. Balance labels within preference categories so a preference is not a proxy
   for a score.
4. Aim initially for at least 50-100 genuinely different examples per label.
5. Prefer collecting or generating diverse examples over duplicating rows.
   Weighted sampling or oversampling is only a temporary fallback.
6. Record teacher model, version, prompt, temperature, seed, and labeling
   timestamp, and review a sample manually for rubric consistency.

## Training objective

Keep response-only loss as the preferred objective for a scorer whose desired
output is the assistant's score. Do not remove it solely because the current
12-epoch response-only run underperformed. That comparison was not controlled:

- the response-only and legacy full-sequence runs used different dataset
  hashes;
- they used different effective batch sizes;
- the response-only data was severely label-imbalanced; and
- token-level validation loss did not measure scoring quality.

Retain native-chat full-sequence loss as a baseline and compare both objectives
on the same job split, dataset hash, seed, effective batch size, LoRA settings,
prompt profile, and checkpoint schedule.

Consider a separate structured-target experiment such as:

```json
{"score": 4, "reason": "Strong backend match but limited cloud experience."}
```

A short rationale supplies more supervised tokens than a single-character
answer, but only use this format if rationale labels are reliable and runtime
output can be constrained and parsed consistently. Keep score-only targets as
the simpler baseline.

## Truncation and prompt diagnostics

The observation that the base model often returns 4 does not by itself prove
that the input is too long. Instrument dataset preparation and validation to
record, for every example:

- original and final token counts;
- whether truncation occurred;
- how many tokens were removed from the preference and job description;
- whether required job sections survived truncation; and
- whether the complete assistant answer plus EOS/end-of-turn token survived.

Continue using answer-preserving truncation. If truncation is common, preserve
the preference, requirements, responsibilities, and relevant candidate
evidence rather than blindly removing the end of the prompt.

## Checkpoint selection

Do not select checkpoints from validation loss alone. Generate predictions on
the leakage-free validation jobs at each checkpoint and make macro-F1 or
balanced accuracy the primary selection metric. Use exact accuracy,
within-one accuracy, MAE, per-label recall, N/A F1, answer coverage, invalid
rate, and prediction distribution as supporting diagnostics.

MAE must be reported together with coverage: a model that emits N/A for many
numeric cases can otherwise appear to have deceptively good numeric MAE.

## Controlled next experiment

After rebuilding the leakage-free, better-balanced dataset, run this small
matrix before another long training job:

| Experiment | Loss mode | Learning rate | Epochs |
| --- | --- | ---: | ---: |
| A | response-only | `5e-5` | 6 |
| B | response-only | `1e-4` | 6 |
| C | response-only | `2e-4` | 4 |
| D | native-chat full-sequence | `1e-4` | 4 |

Use the same seed, data order, effective batch size, LoRA configuration, and
prompt profile in every experiment. Save and evaluate at least once per epoch.
Start with 4-6 epochs; do not repeat a 12-epoch run unless leakage-free task
metrics are still improving at epoch 6.

## Promotion-suite improvements

The 53 promotion cases represent only 25 source jobs, so the observations are
correlated and the effective sample size is smaller than 53. Expand the suite
with more unique jobs and preferences that resemble production traffic. Keep
the suite hidden from dataset generation and routine checkpoint selection.

The current best Qwen checkpoint is the legacy full-sequence
`ai-scorer-qwen25:ksr1-cp300-f16`, but it still fails the promotion gate. Treat
it as the benchmark to beat, not as evidence that full-sequence loss is the
better objective. The next promotion decision should be based on a controlled
comparison over leakage-free data.
