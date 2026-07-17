# Training TODO

The current training workflow is directionally sound, but the following issues
should be addressed before relying on a fine-tuned model for promotion.

## Required actions

1. **Implemented:** identify jobs by versioned job-description fingerprint,
   exclude confirmed and ambiguous promotion matches, split fingerprints before
   expanding future jobs into preferences, and enforce the persisted split at
   export, preflight, and training startup. All 500 paid legacy labels remain
   stored alongside 353 newly labeled full-description cases; 683 eligible
   cases are currently exported.

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

5. **Partially implemented:** the default trainer now equalizes exposure per
   `(job fingerprint, preference key)` and rotates retained alternatives across
   epochs without changing the paid-label inventory. A separate deterministic
   queue produced 353 independently labeled cases across 263 full-description
   jobs, using only the ten preferences in `training_preferences.seed.json`.
   The labels are merged without changing any of the original 500 labels, and
   the new jobs have persisted train/validation assignments. Promotion fixtures
   contribute only job fingerprints to exclude. Next, add boundary examples,
   complete teacher-model provenance, set deterministic labeling parameters,
   and add retry and incremental checkpoint support to the labeling workflow.

6. Fix packaging paths and enforce consistent prompt profiles across dataset
   export, training, GGUF/Ollama packaging, and runtime inference.

## Promotion-fixture isolation

The canonical promotion set contains 53 cases across 25 complete-description
fingerprints. Mongo IDs were not reliable across the servers used to create the
datasets, so they have been removed from maintained schemas and artifacts.

The paid legacy dataset contains 500 labeled cases across 30 original job
descriptions. Reconciliation against full descriptions found 13 confirmed
promotion overlaps and quarantined four additional ambiguous fingerprints.
All affected cases remain in the paid label inventory but are excluded from
train and validation exports. The 13 eligible legacy fingerprints produce 310
training and 20 validation cases. The native full-description expansion adds
321 training and 32 validation cases, for current totals of 631 and 52 across
247 training and 29 validation fingerprints.

## Recommended train/validation design

The generated training artifacts should contain only train and validation
sets. The canonical 53 cases serve as the external promotion test and must not
be used for training, validation, checkpoint selection, early stopping, or
label generation.

1. Exclude confirmed and quarantined promotion fingerprints first.
2. Split approximately 85-90% of the remaining unique jobs into training and
   10-15% into validation.
3. Expand each partition into preference examples only after the fingerprint-level
   split.
4. Store the job fingerprints and dataset hash in the run manifest.
5. Fail preflight validation if train, validation, and promotion contain any
   overlapping job fingerprints.

Exact row counts such as 450/50 are less important than keeping all examples
derived from a job in one partition.

## Dataset distribution and coverage

The current leakage-free training export has the following source distribution:

| Score | Training count | Training share | Promotion count | Promotion share |
| --- | ---: | ---: | ---: | ---: |
| 0 | 163 | 25.8% | 10 | 18.9% |
| 1 | 41 | 6.5% | 0 | 0.0% |
| 2 | 50 | 7.9% | 6 | 11.3% |
| 3 | 65 | 10.3% | 8 | 15.1% |
| 4 | 231 | 36.6% | 10 | 18.9% |
| 5 | 61 | 9.7% | 16 | 30.2% |
| N/A | 20 | 3.2% | 3 | 5.7% |

The expansion materially improves scores 4 and 5, but now overrepresents score
4 and still undersupplies score 5 relative to the promotion set. Before further
long runs:

1. Add diverse, independently sourced examples for scores 4 and 5.
2. Add hard boundary examples for 2 versus 3 and 3 versus 4.
3. Balance labels within preference categories so a preference is not a proxy
   for a score.
4. Aim initially for at least 50-100 genuinely different examples per label.
5. Prefer collecting or generating diverse examples over duplicating rows.
   Weighted sampling or oversampling is only a temporary fallback.
6. Record teacher model, version, prompt, temperature, seed, and labeling
   timestamp, and review a sample manually for rubric consistency.

The current balanced schedule is a temporary correction for repeated legacy
jobs. It resolves exact duplicate model inputs only in a derived training view,
then exposes at most one alternative per `(job fingerprint, preference key)`
per epoch by default. On the current 631-row train export this yields 404 rows
per epoch. Epoch zero contains 55 score-5 examples, a material improvement over
the previous single independent score-5 example, but score 4 accounts for 212
of 404 examples. Further collection should target genuine 2/3, 3/4, and 4/5
boundaries rather than more obvious score-4 matches.

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

The 53 promotion cases represent only 25 unique job fingerprints, so the observations are
correlated and the effective sample size is smaller than 53. Expand the suite
with more unique jobs and preferences that resemble production traffic. Keep
the suite hidden from dataset generation and routine checkpoint selection.

The current best Qwen checkpoint is the legacy full-sequence
`ai-scorer-qwen25:ksr1-cp300-f16`, but it still fails the promotion gate. Treat
it as the benchmark to beat, not as evidence that full-sequence loss is the
better objective. The next promotion decision should be based on a controlled
comparison over leakage-free data.
