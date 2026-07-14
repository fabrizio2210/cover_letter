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
   exact score accuracy, mean absolute error, N/A precision/recall/F1, invalid
   response rate, and score distribution. The canonical 53-case promotion set
   should remain separate from the internal validation set.

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
