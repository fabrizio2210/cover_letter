# Scorer experiments: fp-v2 balanced response checkpoint 200

Date: 2026-07-20

Fixed scoring LLM: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`

Evaluation fixture: `src/python/ai_scorer/evals/data/canonical/v1.json` (53 cases)

Gate thresholds (candidate compared with stored reference metrics):

- exact accuracy drop <= 0.03 (candidate exact accuracy >= 0.5360)
- N/A F1 drop <= 0.05 (candidate N/A F1 >= 0.95)
- mean absolute error increase <= 0.20 (candidate MAE <= 0.76)

All experiments use Ollama at `http://localhost:11434`, temperature 0, and the
repository evaluation runner. No case IDs, expected labels, rationales, tags, or
other golden-only data are available to the scorer.

## Attempt 1: Current scorer baseline

Status: failed

### Strategy and rationale

Establish a clean baseline of the current production scoring path before making
changes. The current path embeds the normalized description with
`BAAI/bge-small-en-v1.5`, retrieves the two chunks with highest cosine similarity
to the raw preference guidance, and asks the fixed scoring LLM for one score.

### Exact configuration

- Command: `EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-01 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Chunking: paragraph/bullet/sentence units, window size 1
- Retrieval: cosine similarity against raw preference guidance, top 2
- LLM temperature: 0
- Request system prompt: enabled
- Packaged model system prompt: the same canonical scorer instruction is also
  embedded in the Ollama model
- User prompt:

  ```text
  Preference Guidance: {preference_guidance}

  Job Title: {job_title}
  Job Location: {job_location}
  Relevant Context Snippets:
  - {retrieved_snippet_1}
  - {retrieved_snippet_2}

  ```

- Canonical system prompt (`SCORING_SYSTEM_INSTRUCTION`):

  ```text
  You are an objective HR analyzer. Evaluate one candidate preference against one job posting using the preference guidance. Prefer a numeric score whenever the posting provides any meaningful evidence. Use N/A only when the posting lacks enough evidence to make a judgment at all. Treat the job title and job location as primary evidence; generic company boilerplate and repeated snippet fragments should not raise a score by themselves. Return either one integer score from 0 to 5, or N/A when the job posting is truly insufficient. Do not return JSON and do not add any explanation text.Scoring rubric:
  - 0 = opposite fit, explicit mismatch, or clearly unsupported
  - 1 = tiny indirect overlap, mostly noise
  - 2 = partial fit, but not a core responsibility
  - 3 = good fit with some direct evidence
  - 4 = strong fit with explicit evidence
  - 5 = exceptional fit where the preference is central and repeatedly supported

  Choose the best matching numeric score from 0 to 5. If there is some evidence, prefer a numeric score over N/A.

  Do not let boilerplate snippets override a weak or conflicting title/location signal.

  Respond only with one number in range 0..5, or N/A only if the posting provides no meaningful evidence at all.
  ```

### Evaluation results

- Environment preflight: the first command used the system Python and stopped
  before model execution with `ModuleNotFoundError: No module named 'ollama'`.
  All reported results below use the repository `.venv` by prefixing its `bin`
  directory on `PATH`.
- Separate fingerprint-disjoint training validation split (52 cases):
  - exact accuracy: 33/52 = 0.6346
  - numeric MAE: 0.3529
  - predicted distribution: 0=1, 1=3, 2=7, 3=11, 4=24, 5=6, N/A=0
  - expected distribution: 0=2, 1=4, 2=3, 3=10, 4=24, 5=8, N/A=1
- Golden gate (53 cases):
  - exact accuracy: 12/53 = 0.2264
  - N/A precision/recall/F1: 1.0/1.0/1.0
  - mean absolute error: 1.2000
  - predicted distribution: 0=4, 1=11, 2=12, 3=12, 4=8, 5=3, N/A=3
  - result: **FAILED** on exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-01/`

### Improvement/regression

The model is reasonably accurate on its disjoint validation preferences, but
regresses by 40.8 percentage points on the golden preferences. N/A detection is
already perfect. The golden distribution exposes a strong low-score bias: only
three 5s are predicted where the expected distribution has sixteen, while
scores 1-3 are overproduced.

### Conclusion and proposed next step

The embedding retriever often returns vague or irrelevant evidence for short
preference guidance such as coding intensity. Increase retrieval depth while
preserving the exact training-time prompt and scoring rubric. This tests whether
broader evidence coverage corrects under-scoring without changing calibration
instructions or N/A policy.

## Attempt 2: Broader dense retrieval (top 6)

Status: failed

### Strategy and rationale

Increase dense retrieval from two to six chunks. A two-snippet bottleneck can
miss core responsibilities, especially when a terse preference and the posting
use different vocabulary. More independently relevant evidence also lets the
rubric distinguish repeated, central support (scores 4-5) from isolated overlap.
This is a general retrieval change and does not use golden labels or metadata.

### Exact configuration

- Command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-02 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Chunking: paragraph/bullet/sentence units, window size 1
- Retrieval: cosine similarity against raw preference guidance, top 6
- LLM temperature: 0
- Request system prompt: enabled
- System and user prompts: unchanged from Attempt 1 except that the relevant
  context block can contain up to six snippets

### Evaluation results

- Golden gate exact accuracy: 12/53 = 0.2264
- N/A precision/recall/F1: 1.0/0.3333/0.5000
- Mean absolute error: 1.0400
- Predicted distribution: 0=2, 1=12, 2=8, 3=12, 4=14, 5=4, N/A=1
- Result: **FAILED** on exact accuracy, N/A F1, and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-02/`

### Improvement/regression

Exact accuracy was unchanged. MAE improved by 0.16, and the broader evidence
shifted several underpredictions closer to their targets. However, irrelevant
extra snippets also raised clear mismatches and converted two correct N/A
predictions to numeric scores, cutting N/A F1 in half. Mean latency increased
from 1,384 ms to 2,218 ms per case.

### Conclusion and proposed next step

Revert retrieval to top 2. The low-score bias remains the primary issue. Clarify
the general rubric so a decisive explicit title, location, work arrangement, or
core duty can earn a 5 without redundant evidence, while preserving the strict
N/A availability decision.

## Attempt 3: Decisive-evidence rubric

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Keep the original top-2 retrieval path and replace ambiguous calibration terms
such as “exceptional” and “repeatedly supported” with an ordinal rubric based on
directness, centrality, limitations, and contradiction. The old score-5 wording
systematically discourages the highest score even when a title or location is
decisive. The new wording also separates missing evidence (N/A) from meaningful
negative evidence (0), which is a general evaluation distinction.

Select or reject this prompt using the separate 52-case fingerprint-disjoint
training validation split before running the golden gate.

### Exact configuration

- Validation command: repository `.venv` Python script invoking Ollama chat over
  `src/python/ai_scorer/training/data/export/val.jsonl`, with each row's system
  message replaced by the prompt below
- Planned gate command if validation is acceptable:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-03 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Chunking: paragraph/bullet/sentence units, window size 1
- Retrieval: cosine similarity against raw preference guidance, top 2
- LLM temperature: 0
- Request system prompt: enabled
- User prompt: unchanged from Attempt 1
- System prompt:

  ```text
  You are an objective HR analyzer. Evaluate how well one job posting matches one candidate preference. Use only the supplied title, location, and relevant context snippets. Treat explicit title, location, and core-duty evidence as more important than generic company or benefits boilerplate. Do not invent requirements or treat an omitted detail as a contradiction.

  First decide whether a score is available. Return N/A only when the title, location, and snippets contain no meaningful evidence either for or against the preference. If there is any meaningful supporting or conflicting evidence, return a numeric score.

  Scoring rubric:
  - 0 = explicit contradiction, opposite fit, or a clearly unrelated role
  - 1 = only faint or tangential overlap
  - 2 = limited support; the preference is secondary or substantially constrained
  - 3 = meaningful but partial or mixed fit
  - 4 = strong, direct fit with a minor limitation or ambiguity
  - 5 = clear, direct, unqualified fit that is central or otherwise decisive

  A single explicit title, location, work-arrangement statement, or core duty can be decisive; repetition is not required. Do not let boilerplate or duplicate fragments inflate the score.

  Respond with exactly one integer from 0 to 5, or N/A. Do not return JSON or explanation text.
  ```

### Evaluation results

- Disjoint validation exact accuracy: 29/52 = 0.5577
- Disjoint validation numeric MAE: 0.4600
- Predicted distribution: 0=1, 1=2, 2=5, 3=8, 4=35, 5=0, N/A=1
- Golden gate: not run because the predeclared validation acceptance condition
  failed

### Improvement/regression

Compared with the Attempt 1 validation baseline, exact accuracy regressed by 7.7
percentage points and MAE increased by 0.107. Despite explicitly clarifying when
to use 5, the model emitted no 5s at all and overproduced 4s.

### Conclusion and proposed next step

Reject the system-prompt rewrite and restore the training-time prompt exactly.
Test whether a minimal calibration reminder in the user message can preserve the
fine-tuned prompt contract while correcting the score-5 bias.

## Attempt 4: Minimal user calibration reminder

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Preserve the original system prompt and exported user-message structure, adding
only a final reminder that the full scale is available and decisive evidence can
earn 5. This isolates whether the model is more responsive to local calibration
than to a rewritten system contract.

### Exact configuration

- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Validation set: `src/python/ai_scorer/training/data/export/val.jsonl`
- LLM temperature: 0
- System prompt and original user prompt: exactly as Attempt 1
- Text appended to every user message:

  ```text
  Calibration reminder: Use the full 0-to-5 scale. A clear, direct, unqualified match that is central or decisive should receive 5; decisive evidence does not need to be repeated.
  ```

### Evaluation results

- Disjoint validation exact accuracy: 25/52 = 0.4808
- Disjoint validation numeric MAE: 0.5490
- Predicted distribution: 0=0, 1=1, 2=6, 3=18, 4=25, 5=2, N/A=0
- Golden gate: not run because validation materially regressed

### Improvement/regression

Exact accuracy regressed by 15.4 percentage points and MAE increased by 0.196
relative to Attempt 1's validation baseline. The reminder also removed all 0 and
N/A outputs. The fine-tuned model is highly sensitive to out-of-distribution
prompt suffixes.

### Conclusion and proposed next step

Reject direct calibration prompting. Preserve the exact final scoring prompt
shape and improve only its two snippet values via a separate evidence-refinement
stage.

## Attempt 5: Dense retrieval plus LLM evidence refinement

Status: failed

### Strategy and rationale

Retrieve ten candidate chunks with embeddings, then use an auxiliary general
instruction model to compress title, location, and candidate chunks into exactly
two faithful evidence sentences: one supporting and one limiting. The fixed
scoring LLM still receives its familiar original system prompt and two-snippet
user prompt. This is retrieval-augmented query-focused summarization, intended to
improve semantic recall without the N/A regression caused by sending all ten
chunks directly.

The auxiliary model is not asked for a score. It only extracts evidence, and the
required checkpoint remains the sole scoring LLM.

### Exact configuration

- Planned gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-05 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Evidence-refinement model: `qwen2.5:1.5b`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Chunking: paragraph/bullet/sentence units, window size 1
- First-stage retrieval: cosine similarity against raw preference guidance,
  top 10
- Evidence refinement: temperature 0, Ollama JSON mode, two one-sentence fields
- Final scoring: original canonical system prompt, original user template, two
  refined snippets, temperature 0
- Evidence-refinement system prompt:

  ```text
  You prepare evidence for a separate job-preference scoring model. Do not assign a score and do not infer facts that are not stated. Use the job title and location as primary evidence, then select and compress only job-specific evidence from the candidate snippets. Separate missing information from contradictory information. For preferences about work arrangement, an explicit location or work-arrangement statement is decisive evidence. Return one JSON object with exactly two string fields: supporting_evidence and limiting_evidence. Each field must be one concise sentence. If a kind of evidence is absent, state that it is absent.
  ```
- Evidence-refinement user prompt:

  ```text
  Preference Guidance: {preference_guidance}

  Job Title: {job_title}
  Job Location: {job_location}
  Candidate Description Snippets:
  1. {candidate_snippet_1}
  ...
  10. {candidate_snippet_10}
  ```

### Evaluation results

- Golden gate exact accuracy: 11/53 = 0.2075
- N/A precision/recall/F1: 0.0/0.0/0.0
- Mean absolute error: 1.1800
- Predicted distribution: 0=4, 1=11, 2=10, 3=18, 4=9, 5=1, N/A=0
- Result: **FAILED** on exact accuracy, N/A F1, and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-05/`

### Improvement/regression

Exact accuracy regressed by 1.9 percentage points, N/A F1 fell from 1.0 to 0,
and MAE improved only 0.02. Inspection of the refined text showed that the
auxiliary 1.5B model sometimes restated preference text as job evidence and
frequently replaced concrete duties with generic title/location paraphrases.
The final scorer then had less faithful evidence than the baseline.

### Conclusion and proposed next step

Reject free-form evidence generation. Limit the auxiliary model to expanding the
preference into a retrieval query; continue to give the scoring model only
verbatim source snippets.

## Attempt 6: LLM-expanded retrieval query

Status: failed

### Strategy and rationale

Use the auxiliary general model only to expand terse preference guidance with
semantic synonyms, relevant titles, duties, technologies, and contradicting
phrases. Embed that expanded query and retrieve the original top two source
chunks. This targets the baseline's observed query-vocabulary gap while
eliminating free-form rewriting of job evidence. The fixed scorer still sees its
exact training-time prompt shape and verbatim posting content.

### Exact configuration

- Planned gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-06 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Query-expansion model: `qwen2.5:1.5b`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Chunking: paragraph/bullet/sentence units, window size 1
- Retrieval: cosine similarity between expanded preference query and chunks,
  top 2
- Query expansion: temperature 0, Ollama JSON mode
- Query expansion cache: in-memory, keyed by expansion model and exact preference
  guidance
- Final scoring prompt and temperature: identical to Attempt 1
- Query-expansion system prompt:

  ```text
  Rewrite a candidate job preference as one concise semantic search query for retrieving evidence from a job description. Include relevant role titles, duties, technologies, synonyms, and both supporting and contradicting phrases when useful. Preserve the preference's meaning and intensity. Do not evaluate any job and do not assign a score. Return one JSON object with exactly one string field named search_query.
  ```
- Query-expansion user prompt:

  ```text
  Preference Guidance: {preference_guidance}
  ```

### Evaluation results

- Golden gate exact accuracy: 16/53 = 0.3019
- N/A precision/recall/F1: 1.0/0.6667/0.8000
- Mean absolute error: 1.0400
- Predicted distribution: 0=2, 1=11, 2=12, 3=14, 4=7, 5=5, N/A=2
- Result: **FAILED** on exact accuracy, N/A F1, and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-06/`

### Improvement/regression

Exact accuracy improved by 7.5 percentage points (four additional exact cases)
and MAE improved by 0.16. Query expansion also increased score-5 predictions
from three to five. However, one correct N/A became numeric, and the candidate
still underproduced the high end of the scale.

### Conclusion and proposed next step

Keep query expansion as a candidate-generation stage, but preserve the baseline
scorer's perfect availability decision. Retrieve ten expanded-query candidates
and constrain the auxiliary model to selecting source indices only, one for
support and one for limitation. Rescore those two verbatim snippets.

## Attempt 7: Availability gate plus source-constrained reranking

Status: failed

### Strategy and rationale

Run the original top-2 scorer first as an availability gate; return immediately
on N/A. For numeric cases, expand the preference query, retrieve ten candidates,
and ask the auxiliary model to choose one supporting and one limiting snippet by
integer index. The auxiliary model cannot rewrite posting evidence or assign a
score. The fixed scorer then makes the final numeric judgment from the two
selected source snippets. Explicit absence markers are used when the selector
finds no support or no limitation.

This combines the baseline's perfect N/A behavior with broader semantic recall
and a source-grounded reranker.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-07 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model for availability and final numeric score:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Query-expansion and snippet-selection model: `qwen2.5:1.5b`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Availability stage: raw preference query, dense top 2, original prompt,
  temperature 0
- Candidate stage: LLM-expanded query, dense top 10
- Reranking stage: JSON `support_index` and `limiting_index`, temperature 0;
  only original source strings can be selected
- Final scoring stage: original prompt with two selected snippets, temperature 0
- Query-expansion prompt: identical to Attempt 6
- Snippet-selection system prompt:

  ```text
  Select source evidence for a separate job-preference scoring model. The preference is a criterion, never job evidence. Do not assign a score, rewrite evidence, or infer unstated facts. Choose the single candidate snippet with the strongest direct support and the single candidate snippet with the strongest contradiction or material limitation. Use index 0 when that kind of evidence is absent. Return one JSON object with exactly two integer fields: support_index and limiting_index.
  ```
- Snippet-selection user prompt:

  ```text
  Preference Guidance: {preference_guidance}

  Job Title: {job_title}
  Job Location: {job_location}
  Candidate Source Snippets:
  1. {candidate_snippet_1}
  ...
  10. {candidate_snippet_10}
  ```
- If `support_index` or `limiting_index` is 0, the corresponding final snippet
  is `(no supporting description evidence selected)` or
  `(no limiting description evidence selected)`.

### Evaluation results

- Golden gate exact accuracy: 13/53 = 0.2453
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.3800
- Predicted distribution: 0=4, 1=16, 2=13, 3=9, 4=7, 5=1, N/A=3
- Result: **FAILED** on exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-07/`

### Improvement/regression

The baseline availability gate restored N/A F1 to 1.0. Exact accuracy improved
by only one case over Attempt 1 and regressed by three cases relative to Attempt
6; MAE worsened to 1.38. The auxiliary selector frequently returned index 0 for
both evidence types, including postings with clearly relevant platform titles,
and the explicit absence markers pushed the scorer toward scores 1-3.

### Conclusion and proposed next step

Keep the availability gate and expanded-query candidate generation. Replace the
abstaining LLM selector with a trained cross-encoder relevance reranker that
always ranks verbatim source text.

## Attempt 8: Availability gate plus cross-encoder reranking

Status: failed

### Strategy and rationale

Use the original scorer as the N/A gate, expand the preference for dense
top-10 candidate generation, and rerank those candidates with a pretrained
cross-encoder against the original natural-language preference. Select the top
two verbatim snippets for the final fixed-model score. Cross-encoders jointly
attend to query and document text and are an established second-stage retrieval
method; unlike Attempt 7's LLM selector, this stage cannot abstain, rewrite, or
hallucinate evidence.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-08 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model for availability and final numeric score:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Query-expansion model: `qwen2.5:1.5b`
- Dense embedding model: `BAAI/bge-small-en-v1.5`
- Cross-encoder reranker: `jinaai/jina-reranker-v1-tiny-en`
- Availability stage: raw preference dense top 2, original prompt, temperature 0
- Candidate stage: expanded preference dense top 10
- Reranking stage: original preference jointly scored against each candidate;
  cross-encoder top 2 selected
- Final scoring stage: original prompt with two verbatim reranked snippets,
  temperature 0

### Evaluation results

Setup note: `BAAI/bge-reranker-base` was considered first, but its 800+ MB model
download was stopped after 4m41s at 20%. No evaluation used that model. The
compact Jina cross-encoder above uses the same algorithm and is the exact model
for this attempt.

- Golden gate exact accuracy: 24/53 = 0.4528
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9400
- Predicted distribution: 0=4, 1=9, 2=12, 3=15, 4=3, 5=7, N/A=3
- Result: **FAILED** on exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-08/`

### Improvement/regression

This was the strongest attempt so far. Relative to Attempt 1, exact accuracy
improved by 22.6 percentage points (12 additional exact cases), MAE improved by
0.26, and N/A F1 remained perfect. Relative to Attempt 7, the cross-encoder
gained eleven exact cases and reduced MAE by 0.44. The remaining errors are
mostly underpredictions at expected scores 4-5 and 1-vs-0 mismatches.

### Conclusion and proposed next step

Keep the pipeline. Add title and location as field-aware, source-grounded
candidates to the cross-encoder stage instead of reranking description chunks
alone. These fields are already declared primary evidence by the scoring
contract and can be decisive even when dense description retrieval is weak.

## Attempt 9: Field-aware cross-encoder reranking

Status: failed

### Strategy and rationale

Extend Attempt 8's reranking pool with `Job Title: ...` and `Job Location: ...`
metadata candidates before cross-encoder ranking. This lets the same semantic
reranker choose primary structured fields when they are more relevant than a
description chunk. The approach is preference-agnostic and applies uniformly to
all job postings.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-09 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Models, prompts, temperature, chunking, availability gate, query expansion,
  dense top-10 retrieval, and cross-encoder: identical to Attempt 8
- Reranking candidates, in order before score sorting:
  1. `Job Title: {job_title}` when non-empty
  2. `Job Location: {job_location}` when non-empty
  3. ten expanded-query dense description candidates
- Final evidence: cross-encoder top 2 verbatim candidates

### Evaluation results

- Golden gate exact accuracy: 21/53 = 0.3962
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9600
- Predicted distribution: 0=3, 1=10, 2=11, 3=16, 4=3, 5=7, N/A=3
- Result: **FAILED** on exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-09/`

### Improvement/regression

Adding metadata candidates lost three exact cases and increased MAE by 0.02
relative to Attempt 8. The compact reranker often continued to prefer generic
description chunks; when metadata was selected, repeating fields already shown
in the user prompt did not improve calibration consistently.

### Conclusion and proposed next step

Revert metadata candidates. Keep Attempt 8's source-grounded cross-encoder
pipeline and increase final reranked evidence depth from two to four chunks so
the scorer can see repeated central support required by its trained rubric.

## Attempt 10: Cross-encoder top-4 evidence

Status: failed

### Strategy and rationale

Keep Attempt 8 unchanged except for selecting the four highest cross-encoder
description chunks rather than two. Candidate generation remains dense top 10,
so added context has passed both dense retrieval and joint query-document
reranking. The original top-2 availability gate prevents extra context from
changing N/A decisions.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-10 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- All models, prompts, temperatures, chunking, availability gate, query
  expansion, dense top-10 retrieval, and reranker are identical to Attempt 8
- Final evidence: cross-encoder top 4 verbatim description chunks

### Evaluation results

- Golden gate exact accuracy: 20/53 = 0.3774
- N/A F1: 0.8571
- Mean absolute error: 0.9592
- Result: **FAILED** all three gate metrics
- Artifacts: `eval-results/experiments/cp200-attempt-10/`

### Improvement/regression

Relative to Attempt 8, exact accuracy fell by 7.5 percentage points (four exact
cases), MAE increased by 0.0192, and N/A F1 fell from 1.0 to 0.8571. Despite the
availability gate, the longer final context caused the numeric scoring pass to
emit one additional N/A. More context did not increase high-score recall.

### Conclusion and proposed next step

Revert to cross-encoder top 2. Rank dense candidates against the expanded
semantic retrieval query instead of the terse original preference. The
expansion contains role, duty, technology, synonym, and contradiction language
that may also give the compact cross-encoder a more discriminative query.

## Attempt 11: Expanded-query cross-encoder reranking

Status: failed

### Strategy and rationale

Keep Attempt 8's two-snippet evidence budget, availability gate, and source-only
evidence. Use the generated semantic query consistently for both dense
candidate retrieval and cross-encoder reranking. The query-expansion prompt was
designed to express titles, duties, technologies, synonyms, and contradictions;
this should help a compact retrieval-trained reranker distinguish direct job
evidence from generic semantic overlap.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-11 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Models, exact prompts, temperature 0, chunking, raw-preference dense top-2
  availability gate, expanded-query dense top-10 candidate retrieval, and
  cross-encoder are identical to Attempt 8
- Reranking query: generated `search_query` rather than raw preference guidance
- Final evidence: cross-encoder top 2 verbatim description chunks

### Evaluation results

- Golden gate exact accuracy: 19/53 = 0.3585
- N/A F1: 0.8571
- Mean absolute error: 1.0816
- Result: **FAILED** all three gate metrics
- Artifacts: `eval-results/experiments/cp200-attempt-11/`

### Improvement/regression

Relative to Attempt 8, exact accuracy fell by 9.4 percentage points (five exact
cases), MAE increased by 0.1416, and N/A F1 fell from 1.0 to 0.8571. The expanded
query improved recall in dense candidate generation but made a poor relevance
query for the compact cross-encoder: expanded concepts sometimes outranked the
posting's most direct wording.

### Conclusion and proposed next step

Restore original-preference reranking and top 2. Compare a second compact,
established MS MARCO cross-encoder using the identical pipeline. This isolates
reranker quality while leaving generation and scoring prompts unchanged.

## Attempt 12: MS MARCO MiniLM cross-encoder

Status: failed

### Strategy and rationale

Keep the strongest Attempt 8 pipeline exactly, but replace the Jina tiny
cross-encoder with `Xenova/ms-marco-MiniLM-L-6-v2`. The strongest run still
occasionally selected boilerplate over an explicit sentence (for example, a
remote-policy sentence), so this experiment tests whether a different compact
passage-ranking model improves evidence precision without changing scoring
calibration.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-12 RERANKING_MODEL=Xenova/ms-marco-MiniLM-L-6-v2 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Query expansion: `qwen2.5:1.5b` with the exact Attempt 6 JSON query prompt
- Dense embedding: `BAAI/bge-small-en-v1.5`, expanded-query top 10
- Cross-encoder: `Xenova/ms-marco-MiniLM-L-6-v2`, raw-preference top 2
- Availability gate and final scorer: original prompt, temperature 0

### Evaluation results

- Golden gate exact accuracy: 16/53 = 0.3019
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.2000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-12/`

### Improvement/regression

Relative to Attempt 8, MiniLM lost eight exact cases and increased MAE by 0.26;
N/A detection remained perfect. An oracle over the Attempt 8 and Attempt 12
numeric outputs still had only 22 correct numeric cases, so the rankers did not
provide useful complementary scoring views.

### Conclusion and proposed next step

Restore the Jina tiny reranker. Test passage-window retrieval: retain the
two-snippet scoring interface, but make retrieval candidates include adjacent
sentences so a selected core duty carries enough surrounding evidence to
establish centrality and intensity.

## Attempt 13: Three-sentence passage windows

Status: failed

### Strategy and rationale

Generate the existing atomic sentence/bullet chunks plus sliding windows of
three adjacent units. Run the strongest query expansion, dense top-10 candidate
generation, and Jina cross-encoder top-2 selection over this mixed pool. Passage
window retrieval is a standard way to retrieve on a focused unit while giving a
reader model the surrounding context. It preserves exactly two final bullets,
unlike Attempt 10, while allowing repeated related duties to show that a
preference is central enough for scores 4-5.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-13 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scoring and query models, exact prompts, temperature, embedding, availability
  gate, dense candidate depth, and final evidence count: identical to Attempt 8
- Chunk pool: atomic units plus sliding windows of 3 adjacent units
- Cross-encoder: restored `jinaai/jina-reranker-v1-tiny-en`, raw preference,
  top 2

### Evaluation results

- Golden gate exact accuracy: 17/53 = 0.3208
- N/A F1: 0.8000
- Mean absolute error: 1.0800
- Result: **FAILED** all three gate metrics
- Artifacts: `eval-results/experiments/cp200-attempt-13/`

### Improvement/regression

Relative to Attempt 8, exact accuracy fell by 13.2 percentage points (seven
cases), MAE increased by 0.14, and N/A F1 fell by 0.20. Because the chunk-window
setting also applied to the preliminary dense retrieval, the availability stage
changed as well as final evidence. The longer passages frequently joined useful
sentences to headings or adjacent boilerplate and reduced precision.

### Conclusion and proposed next step

Restore atomic chunks. Test whether removing the request system message avoids
duplicating the identical system prompt packaged in the Ollama model. Select or
reject this at the disjoint validation stage before another golden run.

## Attempt 14: Packaged system prompt only

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

The model's Ollama `Modelfile` packages the canonical system prompt, and the
scoring request also supplies it. Test whether sending only the user message
restores the single-system training shape and improves calibration. This is a
prompt-transport change only; the user prompt and supplied evidence are
unchanged.

### Exact configuration

- Validation command: repository `.venv` Python invoking Ollama over all 52
  `training/data/export/val.jsonl` rows after removing the request-level system
  message
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Packaged system prompt: canonical scorer prompt in the model `Modelfile`
- User messages: byte-for-byte validation export messages
- Temperature: 0
- Planned golden command if validation improved:
  `EVAL_INCLUDE_SYSTEM_PROMPT=false EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-14 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`

### Evaluation results

- Disjoint validation exact accuracy: 33/52 = 0.6346
- Numeric MAE: 0.3529
- Predicted distribution: 0=1, 1=3, 2=7, 3=11, 4=24, 5=6, N/A=0
- Golden gate: not run because every validation metric and output distribution
  was identical to Attempt 1's request-system baseline

### Improvement/regression

No change. Ollama's effective prompt handling produced identical deterministic
outputs with or without the identical request system message.

### Conclusion and proposed next step

Keep the request contract unchanged. Test source-grounded semantic metadata on
the disjoint validation split: use an auxiliary model to label the relationship
of each verbatim snippet to the preference before the fixed model scores it.

## Attempt 15: Source-grounded evidence relation labels

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Use `qwen2.5:1.5b` only as an evidence annotator. It assigns each supplied
verbatim snippet one relation from `contradiction`, `unrelated`, `indirect`,
`direct`, or `central`; it cannot emit a numeric score or rewrite source text.
Prefix those labels to the original snippets, then let the fixed cp200 model
produce the numeric result with its original prompt. This is metadata
enrichment intended to expose directness and centrality that the compact scorer
has difficulty inferring from terse snippets.

### Exact configuration

- Validation set: 52 fingerprint-disjoint exported validation cases
- Annotation model: `qwen2.5:1.5b`, temperature 0, JSON output
- Annotation system prompt:

  ```text
  You annotate job evidence for relevance to a candidate preference. For each supplied snippet, return exactly one relation in the same order: contradiction, unrelated, indirect, direct, or central. Central means the preference is an explicit core responsibility or work condition; generic company text is not central. Direct means explicit support that may not be core. Contradiction means explicit conflict. Do not score the job, select snippets, rewrite evidence, or add relations. Return one JSON object with exactly one array field named relations.
  ```
- Annotation user input: original validation user message
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Final scorer system/user prompt and temperature: original, except each
  verbatim snippet is prefixed `[<relation> evidence]`

### Evaluation results

- Disjoint validation exact accuracy: 31/52 = 0.5962
- Numeric MAE: 0.4118
- Predicted distribution: 0=0, 1=5, 2=4, 3=11, 4=23, 5=9, N/A=0
- Golden gate: not run because validation regressed

### Improvement/regression

Relative to the unannotated validation baseline, exact accuracy lost two cases
and MAE increased by 0.0588. Labels raised 5-count from 6 to 9 but eliminated
all 0 and N/A outputs, shifting low and unavailable examples in the wrong
direction. The production scorer was not changed for this rejected variant.

### Conclusion and proposed next step

Reject metadata labels. Keep final evidence fully verbatim and retry an
LLM-based second-stage selector without Attempt 7's index-0 abstention option:
force exactly two valid source indices, with the raw availability gate retained.

## Attempt 16: Forced extractive LLM evidence selection

Status: failed

### Strategy and rationale

Attempt 7's selector could return index 0 for insufficient evidence and
overused that option, collapsing numeric scores. Replace it with a pure ranking
contract: `qwen2.5:1.5b` must select exactly two distinct zero-based indices from
the expanded-query dense top 10. It cannot abstain, score, rewrite, or invent
evidence. The fixed scorer's original raw top-2 availability stage remains the
only N/A decision, and the final cp200 prompt receives only the two selected
verbatim chunks.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-16 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability: raw preference, BGE-small dense top 2, fixed cp200 original
  prompt, temperature 0
- Query expansion: Attempt 6's exact `qwen2.5:1.5b` JSON prompt
- Candidates: expanded-query BGE-small dense top 10, atomic chunks
- Selector model: `qwen2.5:1.5b`, temperature 0, JSON
- Selector system prompt:

  ```text
  Rank verbatim job-description snippets by how diagnostic they are for evaluating one candidate preference. Prefer explicit core duties, work conditions, technologies, and contradictions over generic or boilerplate text. You must select the requested number of distinct zero-based indices; never abstain, score the job, rewrite evidence, or invent text. Return one JSON object with exactly one array field named selected_indices.
  ```
- Final scorer: fixed cp200, original prompt, temperature 0, exactly two
  selected verbatim chunks

### Evaluation results

- Golden gate exact accuracy: 14/53 = 0.2642
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.1600
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-16/`

### Improvement/regression

Forcing selection fixed Attempt 7's N/A failure, restoring perfect N/A F1, but
lost ten exact cases and added 0.22 MAE relative to Attempt 8. It gained only one
exact case over Attempt 7. The auxiliary model sometimes surfaced stronger
evidence, but not consistently enough, and retrieval changes alone did not fix
the model's low-score calibration.

### Conclusion and proposed next step

Reject the selector and restore the Attempt 8 cross-encoder. Test
retrieval-augmented few-shot calibration on the disjoint validation split:
retrieve similar examples exclusively from the training partition and let the
fixed scorer evaluate the target after those demonstrations.

## Attempt 17: Training-only semantic few-shot calibration

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Embed each full scorer user prompt and retrieve the three nearest labeled
examples from the fingerprint-disjoint training partition. Add those exact
training user/assistant pairs as demonstrations before the target user message,
then let cp200 produce the target score. Retrieval-augmented in-context examples
are an established way to calibrate a small model to the intended ordinal task.
The golden fixture is not used for the example bank, retrieval, prompt, or
selection decision.

### Exact configuration

- Example bank: all 631 rows in `training/data/export/train.jsonl`
- Validation targets: all 52 rows in the fingerprint-disjoint
  `training/data/export/val.jsonl`
- Retrieval representation: full exported user prompt
- Embedding model: `BAAI/bge-small-en-v1.5`, cosine similarity, top 3
- Message order: canonical system, three exact training user/assistant pairs,
  target user
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Temperature: 0
- All scoring and demonstration prompts: exact exported prompts; no added rubric
  or metadata text

### Evaluation results

- Disjoint validation exact accuracy: 24/52 = 0.4615
- Numeric MAE: 0.6458
- Predicted distribution: 0=0, 1=4, 2=8, 3=10, 4=20, 5=7, N/A=3
- Golden gate: not run because validation materially regressed

### Improvement/regression

Relative to the zero-shot validation baseline, few-shot calibration lost nine
exact cases, increased MAE by 0.2929, and introduced three N/A outputs. The
response-only checkpoint treated multiple user/assistant demonstrations as an
out-of-distribution conversation rather than stable task calibration.

### Conclusion and proposed next step

Reject few-shot messages. Check whether the local Ollama API exposes first-token
log probabilities and, if supported, test probability-weighted decoding without
changing the trained prompt or fitting anything to golden labels.

## Attempt 18: Probability-weighted ordinal decoding

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Request the top 20 first-token log probabilities from the fixed scorer. Preserve
greedy N/A outputs. For a numeric response, normalize the probabilities of
tokens 0 through 5, compute their expected ordinal value, and round to the
nearest integer. Expected-value decoding uses uncertainty across adjacent
ordinal labels instead of discarding it at greedy argmax, with no learned
thresholds, mappings, preference logic, or golden data.

### Exact configuration

- Validation targets: all 52 fingerprint-disjoint exported validation cases
- Model and messages: fixed cp200, exact exported system/user messages
- Ollama API: `/api/chat`, `logprobs=true`, `top_logprobs=20`
- Temperature: 0
- N/A rule: preserve the model's explicit greedy N/A
- Numeric rule: `round(sum(score * P(score)) / sum(P(score)))`, with
  conventional half-up rounding and range 0..5
- No parameters are fitted from train, validation, or golden labels

### Evaluation results

- Greedy disjoint validation: 33/52 exact, 0.3529 numeric MAE
- Probability-mean validation: 33/52 exact, 0.3529 numeric MAE
- Both predicted distributions: 0=1, 1=3, 2=7, 3=11, 4=24, 5=6,
  N/A=0
- Golden gate: not run because every decoded result was unchanged

### Improvement/regression

No change on any of 52 held-out cases. The fixed checkpoint's first-token
distribution was sufficiently concentrated that rounding its expected ordinal
value always reproduced the greedy label.

### Conclusion and proposed next step

Reject the added API complexity. Make one bounded follow-up to Attempt 17 using
only the single nearest training example; if it does not beat the zero-shot
validation baseline, abandon in-context calibration.

## Attempt 19: Training-only semantic one-shot calibration

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Repeat Attempt 17 with exactly one nearest training demonstration rather than
three. A single example retains semantic task calibration while reducing prompt
length and multi-turn distribution shift for the response-only checkpoint.

### Exact configuration

- Example bank, validation targets, representation, embedding model, similarity,
  scorer, exact messages, and temperature: identical to Attempt 17
- Retrieval depth: top 1
- Message order: canonical system, one exact training user/assistant pair,
  target user

### Evaluation results

- Disjoint validation exact accuracy: 25/52 = 0.4808
- Numeric MAE: 0.6250
- Predicted distribution: 0=0, 1=3, 2=8, 3=10, 4=19, 5=9, N/A=3
- Golden gate: not run because validation materially regressed

### Improvement/regression

One-shot lost eight exact cases and increased MAE by 0.2721 relative to
zero-shot. It improved by one exact case over three-shot but retained the same
three false N/A outputs. Even one demonstration is out of distribution for this
checkpoint.

### Conclusion and proposed next step

Abandon in-context calibration. Address the observed guidance distribution
shift instead: the model was trained primarily on detailed preferences, while
many target preferences are terse. Test meaning-preserving expanded guidance in
the scorer's preference field.

## Attempt 20: Expanded scoring guidance

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Use the exact query-expansion output not only for retrieval, but as the
`Preference Guidance` presented to the fixed scoring model. Training examples
usually express a focus plus qualifiers such as intensity, maintainability, or
contradictions; terse target guidance omits that vocabulary. Semantic
normalization can put both into a richer but meaning-preserving representation.
The expander sees no job content and cannot evaluate or score a case.

### Exact configuration

- Validation set: 52 fingerprint-disjoint exported cases
- Expansion model/prompt/temperature: exact Attempt 6 configuration
- Scoring model: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scorer prompt: exact exported system/user prompt, replacing only the original
  preference-guidance value with `search_query`
- Job title, location, and both source snippets: unchanged and verbatim
- Temperature: 0

### Evaluation results

- Disjoint validation exact accuracy: 25/52 = 0.4808
- Numeric MAE: 0.5686
- Predicted distribution: 0=0, 1=0, 2=7, 3=12, 4=28, 5=5, N/A=0
- Golden gate: not run because validation materially regressed

### Improvement/regression

Exact accuracy lost eight cases and MAE increased by 0.2157. Search-oriented
expansion added supporting and contradicting synonyms; the scorer treated that
extra vocabulary as overlap, eliminated all 0/1 predictions, and overproduced
4. The same representation should not serve both retrieval recall and scoring.

### Conclusion and proposed next step

Keep search expansion only for retrieval. Test a narrower preference
normalization prompt that preserves intensity but explicitly forbids added role,
technology, duty, synonym, and domain concepts.

## Attempt 21: Explicit evaluation-criterion normalization

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Rewrite the preference as an evaluation criterion rather than a search query.
The normalizer may make intensity and the logically implied opposite condition
explicit, but it cannot broaden the preference with new titles, technologies,
duties, synonyms, or domain concepts. This should turn terse guidance into the
detailed criterion style seen in training without creating artificial overlap.

### Exact configuration

- Validation, normalization model, scorer, messages, source evidence, and
  temperatures: identical to Attempt 20
- Normalization system prompt:

  ```text
  Rewrite one candidate job preference as one explicit evaluation criterion. Preserve its exact meaning, scope, qualifiers, and intensity. State the positive condition and, only when logically implied by the preference, the opposite condition. Do not add technologies, role titles, duties, synonyms, or domain concepts that the preference does not state or directly imply. Do not evaluate any job or assign a score. Return one JSON object with exactly one string field named evaluation_guidance.
  ```

### Evaluation results

- Disjoint validation exact accuracy: 25/52 = 0.4808
- Numeric MAE: 0.5490
- Predicted distribution: 0=0, 1=1, 2=5, 3=20, 4=22, 5=4, N/A=0
- Golden gate: not run because validation materially regressed

### Improvement/regression

Exact accuracy again lost eight cases and MAE increased by 0.1961. Forbidding
new concepts reduced the search expansion's upward bias, but rewriting still
removed all 0 outputs and substantially overproduced score 3. The checkpoint is
better calibrated on the original preference wording.

### Conclusion and proposed next step

Reject all guidance rewriting. Test a uniform metadata-only final pass while
retaining the original evidence-based availability gate. The scoring contract
declares job title and location primary evidence, and target rationales often
turn on those fields while retrieved chunks add misleading adjacent detail.

## Attempt 22: Availability gate plus metadata-only scoring

Status: failed

### Strategy and rationale

Use the original raw-preference dense top-2 score only as an availability gate.
For every numeric case, call the fixed scorer again with the original preference,
job title, job location, and the standard `(no relevant snippets available)`
marker. This uniformly tests whether the declared primary structured fields are
more reliable than noisy chunks. There are no preference-key branches or
metadata interpretations in code.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-22 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability: original preference, BGE-small dense top 2 atomic chunks,
  canonical prompt, fixed cp200, temperature 0
- Final scoring: original preference/title/location, no description snippets,
  canonical prompt and standard empty-evidence marker, fixed cp200, temperature 0
- No query expansion, auxiliary model, cross-encoder, prompt rewrite, score
  aggregation, or case-dependent logic in the final pass

### Evaluation results

- Golden gate exact accuracy: 10/53 = 0.1887
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.7800
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-22/`

### Improvement/regression

Relative to Attempt 8, exact accuracy fell by 26.4 percentage points and MAE
increased by 0.84. The standard `(no relevant snippets available)` marker acted
as strong negative/uncertain evidence: even explicit remote locations and clear
role-family titles were under-scored. N/A stayed perfect only because of the
availability gate.

### Conclusion and proposed next step

Reject metadata-only scoring. Test an overall qualitative relationship label on
the disjoint validation set while retaining verbatim evidence. Unlike Attempt
15's independent per-snippet labels, an overall label can distinguish explicit
conflict, an identifiable unsupported role, partial support, direct support,
central support, and genuinely insufficient evidence.

## Attempt 23: Overall qualitative evidence relationship

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Ask `qwen2.5:1.5b` to assign one overall evidence relationship category using
the original preference, title, location, and two verbatim snippets. Prefix the
category to the first unchanged snippet and let cp200 produce the numeric score.
The auxiliary model cannot emit numbers or rewrite evidence. This supplies the
directness/centrality metadata that the small fixed scorer struggles to infer,
while retaining its ordinal judgment.

### Exact configuration

- Validation: all 52 fingerprint-disjoint exported cases
- Annotator: `qwen2.5:1.5b`, temperature 0, JSON
- Categories: `conflicting_or_opposite`, `no_meaningful_support`,
  `some_but_limited_support`, `direct_substantial_support`,
  `decisive_central_support`, `insufficient_evidence`
- Scorer: fixed cp200, original system/user prompt and temperature; first
  verbatim snippet prefixed `[Overall evidence relationship: <category>]`
- No numeric score, rewritten evidence, or golden content is available to the
  annotator

### Evaluation results

- Disjoint validation exact accuracy: 29/52 = 0.5577
- Numeric MAE: 0.6275
- Predicted distribution: 0=2, 1=4, 2=2, 3=9, 4=29, 5=6, N/A=0
- Golden gate: not run because validation materially regressed

### Improvement/regression

Relative to raw validation, the overall label lost four exact cases and
increased MAE by 0.2746. It restored two score-0 outputs but pushed several mid
scores to extremes and still missed the unavailable case. The qualitative label
was too close to a pre-judgment and made cp200 less calibrated.

### Conclusion and proposed next step

Reject all auxiliary metadata. Test source completeness directly: after the
stable availability gate, give cp200 the normalized full description as one
evidence block. Canonical descriptions are at most 10,027 characters, which
fits the model context with the scorer prompt.

## Attempt 24: Full-description scoring after availability gate

Status: failed

### Strategy and rationale

Use the original raw top-2 stage for N/A. For numeric cases, replace retrieved
snippets with one source-grounded block containing the complete normalized job
description. The reference rationales sometimes depend on overall role scope
(management versus hands-on work, balanced versus central duties) that no two
isolated snippets can reliably preserve. Full-context scoring removes retrieval
recall and chunk-fragmentation errors without summaries or generated metadata.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-24 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability: original preference, BGE-small dense top 2 atomic chunks,
  canonical prompt, fixed cp200, temperature 0
- Final scoring: original preference/title/location plus one evidence block with
  the complete `normalize_description_markdown` output
- Canonical description length: 0 to 10,027 characters; median 4,455
- No auxiliary LLM, generated text, metadata label, score combination, or
  preference-dependent logic

### Evaluation results

- Golden gate exact accuracy: 17/53 = 0.3208
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-24/`

### Improvement/regression

Relative to Attempt 8, full context lost seven exact cases and increased MAE by
0.06. It improved several scope-sensitive coding scores, but boilerplate and
many unrelated duties diluted decisive evidence. Long descriptions required
roughly 10-18 seconds of prompt prefill versus sub-second reranked prompts.

### Conclusion and proposed next step

Reject full context and restore Attempt 8. Upgrade only the compact
cross-encoder from Jina tiny to Jina turbo, a higher-capacity model in the same
supported family, to test whether better passage ranking closes the remaining
evidence gap.

## Attempt 25: Jina turbo cross-encoder

Status: failed

### Strategy and rationale

Keep the strongest source-grounded Attempt 8 pipeline and swap its
`jina-reranker-v1-tiny-en` cross-encoder for
`jinaai/jina-reranker-v1-turbo-en`. The change increases second-stage ranking
capacity without altering candidate generation, evidence count, prompts, or
scoring. This is an isolated retrieval-quality experiment.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-25 RERANKING_MODEL=jinaai/jina-reranker-v1-turbo-en make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability, query expansion, dense model/top-10 retrieval, atomic chunking,
  raw-preference reranking query, final top-2 evidence, scorer prompts, fixed
  cp200 model, and temperature: identical to Attempt 8
- Cross-encoder: `jinaai/jina-reranker-v1-turbo-en`

### Evaluation results

- Golden gate exact accuracy: 18/53 = 0.3396
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0400
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-25/`

### Improvement/regression

Relative to Jina tiny, turbo lost six exact cases and increased MAE by 0.10;
N/A remained perfect. It ranked some explicit contradictions better but was less
consistent on direct support. The smaller model remains the strongest reranker.

### Conclusion and proposed next step

Restore Jina tiny. Isolate dense candidate recall by replacing BGE-small with the
quantized Nomic v1.5 embedding model while leaving expansion, top-10 depth,
reranking, evidence count, and scoring unchanged.

## Attempt 26: Nomic dense candidate retrieval

Status: failed

### Strategy and rationale

Use `nomic-ai/nomic-embed-text-v1.5-Q`, a higher-capacity modern semantic
embedding model, for both the raw availability retrieval and expanded-query
top-10 candidate generation. Keep the strongest Jina tiny cross-encoder for
final ranking. This tests whether the top-10 pool currently misses decisive
chunks before reranking.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-26 EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5-Q make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Pipeline, prompts, fixed scorer, query expansion, depths, chunking, and
  temperature: identical to Attempt 8
- Dense model: `nomic-ai/nomic-embed-text-v1.5-Q`
- Cross-encoder: restored `jinaai/jina-reranker-v1-tiny-en`

### Evaluation results

- Golden gate exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-26/`

### Improvement/regression

Nomic lost one exact case and increased MAE by 0.06 relative to BGE-small/Jina
tiny, while preserving N/A. Its 23 exact cases nevertheless indicate substantial
but different candidate coverage rather than a uniformly worse representation.

### Conclusion and proposed next step

Keep BGE-small for availability. Union BGE-small and Nomic expanded-query top-10
candidates, deduplicate exact text, and let Jina tiny rerank the combined pool.
This uses complementary recall without fusing embedding scores.

## Attempt 27: Multi-retriever candidate union

Status: failed

### Strategy and rationale

Retrieve expanded-query top-10 atomic chunks independently with BGE-small and
quantized Nomic, form their stable verbatim union, and rerank the combined pool
with Jina tiny against the raw preference. Cross-encoder ranking makes scores
from the two dense vector spaces directly comparable only after candidate
generation. This is a standard multi-retriever recall pattern.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-27 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability: original BGE-small raw-preference top 2 and fixed cp200
- Candidate retrievers: `BAAI/bge-small-en-v1.5` top 10 plus
  `nomic-ai/nomic-embed-text-v1.5-Q` top 10
- Fusion: exact-text stable deduplication; no dense-score fusion
- Reranking/final scoring: Jina tiny raw-preference top 2, canonical prompt,
  fixed cp200, temperature 0
- All other settings identical to Attempt 8

### Evaluation results

- Golden gate exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-27/`

### Improvement/regression

The union lost two exact cases and increased MAE by 0.08 relative to the
BGE-only Attempt 8, and lost one exact case while increasing MAE by 0.02 relative
to Nomic-only. Although it surfaced some new decisive evidence, the larger pool
also admitted distractors that the compact cross-encoder ranked too highly.

### Conclusion and proposed next step

Restore BGE-only candidates. Use the fixed cp200 model itself to probe a small
Jina-shortlisted set and select contrastive strongest/weakest source evidence
before a final score. This follows the allowed LLM reranking pattern and avoids
direct score aggregation.

## Attempt 28: Fixed-model contrastive evidence reranking

Status: failed

### Strategy and rationale

After the stable availability gate, expand the query, retrieve BGE top 10, and
take Jina tiny's top 6. Ask the required cp200 scorer to evaluate each verbatim
snippet individually with the canonical prompt. Use those probe scores only to
select the strongest and weakest distinct evidence snippets; cp200 then makes a
fresh final judgment over that contrastive pair. Strong/weak contrast exposes
both central support and limitations without generated text, case rules, or
final-score arithmetic.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-28 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability and expanded-query BGE top-10 candidate generation: Attempt 8
- Cross-encoder shortlist: Jina tiny raw-preference top 6
- Evidence probes: fixed cp200, canonical prompt, temperature 0, one verbatim
  snippet per call
- Selection: highest numeric probe and lowest numeric probe at distinct indices;
  stable candidate order breaks ties; unavailable probes are ignored
- Final scoring: fixed cp200, canonical prompt, temperature 0, selected two
  verbatim snippets
- Probe scores are not averaged, mapped, or returned as the final score

### Evaluation results

- Golden gate exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9600
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-28/`

### Improvement/regression

Contrastive probing lost one exact case and increased MAE by 0.02 relative to
Attempt 8, while preserving N/A. It improved over most other variants, and
probes showed meaningful evidence separation, but the weakest selected snippet
dampened genuinely strong roles more often than it corrected over-scoring.

### Conclusion and proposed next step

Keep the same fixed-model probes but select the two strongest distinct snippets
instead of strongest/weakest. This is the direct LLM top-2 reranking pattern and
should avoid injecting an intentionally weak passage into strong-role prompts.

## Attempt 29: Fixed-model top-2 evidence reranking

Status: failed

### Strategy and rationale

Use Attempt 28's availability gate, expanded-query BGE candidates, Jina top-6
shortlist, and one-snippet cp200 probes. Rank the six verbatim snippets by their
cp200 numeric probe scores and select the two strongest, with stable Jina order
breaking ties. Make a fresh final cp200 call over those two snippets. Probe
scores remain evidence-ranking signals only and are never aggregated into the
returned score.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-29 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- All models, prompts, retrieval stages, depths, evidence probes, temperature,
  validation, and fallback behavior: identical to Attempt 28
- Final selection: top 2 numeric probes descending; stable cross-encoder order
  breaks ties

### Evaluation results

- Golden gate exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-29/`

### Improvement/regression

Top-2 probes reduced MAE by 0.04 versus contrastive probing but lost one exact
case. Relative to direct Jina top-2, it lost two exact cases while improving MAE
by only 0.02, at several times the inference cost. Probe-based reranking is not
worth retaining.

### Conclusion and proposed next step

Restore direct Jina top-2. A pre-existing older checkpoint follows qualitative
evidence-critic instructions; test it only as a non-scoring critic on disjoint
validation, with cp200 still producing the sole numeric score.

## Attempt 30: Older-checkpoint qualitative evidence critic

Status: rejected on disjoint validation

### Strategy and rationale

Use `ai-scorer-qwen25:ksr1-cp300-f16` as a qualitative critic that must return
one relationship category plus a verbatim evidence quote and cannot emit a
number. Prefix only its relationship category to the first original snippet,
then let the required cp200 checkpoint produce the numeric score. The older
checkpoint is not a scorer in this pipeline; its prior standalone scores are
not read, combined, mapped, or exposed to cp200.

### Exact configuration

- Validation: all 52 fingerprint-disjoint exported cases
- Critic model: `ai-scorer-qwen25:ksr1-cp300-f16`, temperature 0, JSON request
- Critic categories: `conflicting`, `unsupported`, `partial`, `direct`,
  `central`, `insufficient`
- Critic input: original preference/title/location/two verbatim snippets
- Schema handling: one repair retry on malformed JSON or an out-of-vocabulary
  relationship, then an unannotated original-prompt passthrough; no synonym
  mapping or case-specific fallback
- Scorer: fixed cp200, canonical prompt, temperature 0; first original snippet
  prefixed `[Evidence critic relationship: <category>]`
- Critic numeric outputs are forbidden; source quote is required; no golden data
  is used

### Evaluation results

- Held-out exact accuracy: 29/52 = 0.5577
- Held-out numeric MAE: 0.4510
- Output distribution: 1=3, 2=9, 3=11, 4=22, 5=7; no 0 or N/A
- Golden evaluation: not run

### Improvement/regression

The critic tied the earlier base-Qwen qualitative relation experiment at 29
exact, but lost four exact cases relative to the canonical 33/52 validation
baseline and increased MAE from 0.3529 to 0.4510. The category prefix often
shifted an otherwise-correct cp200 result by one point. The critic also failed
its output schema on several examples; one repair retry followed by unannotated
passthrough handled this without label mapping.

### Conclusion and proposed next step

Reject without golden evaluation. Restore cp200-only decisions and test a
decomposed self-evaluation on held-out data: have cp200 score each supplied fact
independently, expose those model-generated assessments with the untouched
facts, and ask cp200 for the sole final score. Unlike top-2 probe selection,
this preserves both positive and limiting evidence for final adjudication.

## Attempt 31: cp200 single-fact self-decomposition

Status: rejected on disjoint validation

### Strategy and rationale

Score each of the two supplied facts independently with the required cp200
model and canonical prompt. Preserve both original facts, append each cp200
probe as a diagnostic annotation, and ask cp200 for a fresh holistic score.
This is self-decomposition rather than arithmetic aggregation: no rule combines
the probes, and cp200 remains the only model that emits assessments and the sole
model that chooses the returned score.

### Exact configuration

- Validation: all 52 fingerprint-disjoint exported cases
- Probe model: fixed cp200, canonical system/user prompt, temperature 0, one
  original fact per call
- Final model: fixed cp200, canonical system prompt, temperature 0
- Final evidence: both untouched facts, each suffixed
  `[cp200 single-fact assessment: <probe output>]`
- Final instruction suffix: `The bracketed assessments are independent
  diagnostic readings from this same model. Make a fresh holistic judgment
  from the title, location, and all original facts.`
- No averaging, thresholds, mappings, case rules, or auxiliary model

### Evaluation results

- Held-out exact accuracy: 26/52 = 0.5000
- Held-out numeric MAE: 0.5098
- Output distribution: 1=5, 2=3, 3=16, 4=22, 5=6; no 0 or N/A
- Golden evaluation: not run

### Improvement/regression

Self-decomposition lost seven exact cases and increased MAE by 0.1569 relative
to canonical held-out scoring. The independent probes were noisy, and their
numeric annotations anchored the final response rather than helping cp200
reconcile evidence.

### Conclusion and proposed next step

Reject without golden evaluation. Restore Attempt 8's direct Jina top-2 path
and investigate complementary lexical/sparse retrieval or stronger supported
cross-encoders. Keep any new stage evidence-only; do not annotate final prompts
with intermediate scores.

## Attempt 32: BM25+BGE reciprocal-rank fusion

Status: failed

### Strategy and rationale

Combine lexical and semantic recall using reciprocal-rank fusion (RRF), a
standard hybrid-retrieval technique. Retrieve the expanded query's top 20
atomic source chunks independently with BM25 and BGE-small, fuse to ten without
comparing incomparable raw scores, then apply Attempt 8's proven Jina tiny
raw-preference reranker and fixed cp200 availability/final scoring stages.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-32 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability: raw preference, BGE-small top 2, fixed cp200 canonical prompt
- Expansion: `qwen2.5:1.5b`, Attempt 8's exact JSON search-query prompt,
  temperature 0
- Dense retrieval: `BAAI/bge-small-en-v1.5`, expanded-query top 20 atomic chunks
- Lexical retrieval: in-process BM25 with lowercase alphanumeric tokens,
  `k1=1.5`, `b=0.75`, expanded-query top 20 atomic chunks
- Fusion: RRF with rank constant 60, stable source-order tie breaking, top 10
- Reranking: `jinaai/jina-reranker-v1-tiny-en`, raw preference, top 2
- Final scoring: fixed cp200, canonical prompt, temperature 0, two verbatim
  source snippets
- No score fusion, prompt annotation, case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0400
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-32/`

### Improvement/regression

Hybrid fusion lost two exact cases and increased MAE by 0.10 relative to
Attempt 8. Expanded queries contain common terms, so BM25 elevated lexically
matching boilerplate; RRF then displaced useful semantic-only candidates before
cross-encoding.

### Conclusion and proposed next step

Reject BM25 fusion and restore BGE-small expanded-query top 10. Resume the
partially cached `BAAI/bge-reranker-base` cross-encoder and compare its direct
top 2 with Jina tiny. This changes only evidence ranking and uses a stronger
established relevance model.

## Attempt 33: BGE base cross-encoder reranking

Status: failed

### Strategy and rationale

Restore Attempt 8's semantic candidate generation and replace compact Jina tiny
with the substantially larger `BAAI/bge-reranker-base` cross-encoder. The model
is designed specifically for passage reranking and may distinguish explicit
job duties from adjacent semantic boilerplate more reliably.

### Exact configuration

- Availability, query expansion, BGE-small top-10 candidate retrieval, atomic
  chunks, final fixed-cp200 prompt, and temperature: identical to Attempt 8
- Reranker: `BAAI/bge-reranker-base`, raw preference query, direct top 2
- No lexical fusion, score fusion, generated evidence, or prompt annotations
- Gate command after model preparation:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-33 RERANKING_MODEL=BAAI/bge-reranker-base make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`

### Evaluation results

- Golden gate exact accuracy: 19/53 = 0.3585
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0600
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-33/`

### Improvement/regression

BGE base lost five exact cases and increased MAE by 0.12 relative to Jina tiny.
It often ranked broadly related prose above explicit preference evidence, so its
larger size did not translate into better passage selection for this domain.

### Conclusion and proposed next step

Reject and keep Jina tiny. Measure error complementarity between raw dense and
expanded/reranked evidence views. If their correct cases differ materially,
test a cp200 adjudication call over both verbatim views and their independent
cp200 assessments; do not combine scores arithmetically.

## Attempt 34: Qualitative relation and coverage metadata

Status: failed

### Strategy and rationale

Attempt 8 and full-description scoring have 31 correct cases in their oracle
union, demonstrating complementary local and global signals, but selecting
between numeric results with a rule is prohibited. Instead, ask the older
checkpoint only for constrained qualitative metadata over Attempt 8's two
verbatim facts: relationship strength, evidence coverage, and title signal.
Pass those advisory categories to the required cp200 model, which verifies them
against the original facts and remains the only model that emits a number.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-34 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability, Qwen query expansion, BGE-small expanded top 10, Jina tiny raw
  preference top 2: identical to Attempt 8
- Qualitative critic: `ai-scorer-qwen25:ksr1-cp300-f16`, temperature 0, JSON;
  exact relationship values `contradictory|unrelated|weak|partial|direct|central`,
  coverage `none|single|multiple`, title signal `conflicts|neutral|supports`, plus
  one required verbatim quote
- Schema handling: one repair retry, then empty metadata passthrough; no synonym
  mapping, numerical output, or score use
- Final scorer: fixed cp200, canonical prompt, temperature 0, original Jina top-2
  facts plus `Qualitative evidence metadata (advisory; verify against the source
  facts):` and the three categories
- No deterministic score fusion, mappings, thresholds, or case rules

### Evaluation results

- Golden gate exact accuracy: 19/53 = 0.3585
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.5200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-34/`

### Improvement/regression

Qualitative metadata lost five exact cases and increased MAE by 0.58 relative
to Attempt 8. The critic frequently failed its schema; worse, some conforming
responses inverted explicit facts (for example, marking direct remote evidence
as contradictory), and cp200 trusted the bad annotation.

accuracy and MAE while preserving perfect availability classification.
### Conclusion and proposed next step

Reject and remove the critic. Keep cp200 as the only evaluator. Revisit
in-context calibration from the fingerprint-disjoint training corpus, focusing
on exact message structure and examples balanced across ordinal labels rather
than nearest-neighbor examples that cluster at score 4.

## Attempt 35: Older-checkpoint passage reranking

Status: failed

### Strategy and rationale

Use the older checkpoint strictly as an LLM passage reranker, an explicitly
allowed multi-stage retrieval pattern. After expanded-query BGE top 10 and Jina
top 6, assess each verbatim passage independently with the canonical scorer
contract, rank passages by that relevance signal, and discard every probe
score. The required cp200 checkpoint then receives the two selected verbatim
passages and makes a fresh, sole returned score.

### Exact configuration

- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-35 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability: BGE-small raw-preference top 2, fixed cp200, canonical prompt,
  temperature 0
- Expansion/candidates: Attempt 8's `qwen2.5:1.5b` query, BGE-small top 10
- First reranker: Jina tiny, raw preference, top 6
- LLM passage reranker: `ai-scorer-qwen25:ksr1-cp300-f16`, canonical prompt,
  temperature 0, one verbatim source passage per call
- Selection: two highest available passage probe scores, stable Jina rank for
  ties; probe values are discarded and never shown to the final scorer
- Final scoring: required cp200, canonical prompt, temperature 0, selected two
  source passages; cp200 is the only model whose score is returned
- No score averaging, calibration mappings, metadata annotation, case rules,
  or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9400
- Result: **FAILED** exact accuracy
- Artifacts: `eval-results/experiments/cp200-attempt-35/`

### Improvement/regression

Older-checkpoint probes matched Attempt 8's MAE but lost two exact cases. They
matched Attempt 29's exact count while worsening its MAE by 0.02. A stronger
standalone checkpoint did not produce a better passage relevance ordering.

### Conclusion and proposed next step

Reject and restore direct Jina top 2. Inventory locally available instruction
models for a stronger non-evaluative query expansion or extractive selector;
avoid further score-probe reranking.

## Attempt 36: Qwen2.5 7B extractive selector

Status: aborted during model preparation

### Strategy and rationale

The installed 1.5B instruction model was unreliable as an extractive selector
in Attempt 16. Prepare the standard Qwen2.5 7B instruction model and use it only
to select exactly two source indices from Attempt 8's expanded-query BGE top 10.
The larger model should follow the constrained ranking contract and distinguish
core duties, contradictions, and boilerplate more reliably.

### Exact configuration

- Availability, query expansion, BGE-small embedding, candidate depth, chunks,
  final fixed cp200 prompt, and temperature: identical to Attempt 8
- Selector: `qwen2.5:7b`, temperature 0, JSON, exactly two distinct indices
- Selector prompt: Attempt 16's exact forced extractive ranking system/user
  contract; no scoring, rewriting, generated evidence, or abstention
- Fallback: direct Jina tiny top 2 on invalid selector output or model error
- Final scoring: required cp200, canonical prompt, temperature 0, two selected
  verbatim source passages
- Planned gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-36 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`

### Evaluation results

- Golden evaluation: not run
- Model preparation stopped after about 0.75/4.7 GB; the partial Ollama blob is
  retained and resumable

### Improvement/regression

No scorer result. At the observed network rate, the remaining transfer plus 7B
per-case inference would delay feedback substantially.

### Conclusion and proposed next step

Use the same selector design with Qwen2.5 3B, which still doubles the base
selector's parameter count while reducing preparation and inference cost.

## Attempt 37: Qwen2.5 3B extractive selector

Status: failed

### Strategy and rationale

Repeat Attempt 36's evidence-only selector design with Qwen2.5 3B. It is twice
the failed 1.5B selector's capacity while small enough for a faster experiment.

### Exact configuration

- Identical to Attempt 36 except selector model `qwen2.5:3b`
- Fallback: Jina tiny raw-preference top 2 on any invalid selector response
- Gate command:
  `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-37 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`

### Evaluation results

- Golden gate exact accuracy: 16/53 = 0.3019
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.1200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-37/`

### Improvement/regression

The 3B selector improved by two exact cases and 0.04 MAE over the 1.5B selector,
and followed the exact-index schema reliably, but lost eight exact cases and
added 0.18 MAE relative to Jina tiny. Instruction-following capacity was not the
main limitation: semantic relatedness still did not equal diagnostic evidence.

### Conclusion and proposed next step

Reject extractive selection and restore direct Jina top 2. Test Qwen2.5 3B as a
source-grounded support/limitation analyst on the fingerprint-disjoint
validation split before any golden run. Require verbatim quotes and prohibit
numeric scoring.

## Attempt 38: Qwen2.5 3B support/limitation analysis

Status: rejected on disjoint validation

### Strategy and rationale

Use the stronger instruction model to organize already supplied evidence rather
than select it. It must return verbatim supporting and limiting facts plus one
short centrality statement, with no number. Append this advisory analysis to
the unchanged source prompt and let cp200 make the sole score. Validate first
on 52 fingerprint-disjoint exported cases.

### Exact configuration

- Validation: all 52 exported fingerprint-disjoint cases
- Analyst: `qwen2.5:3b`, temperature 0, JSON
- Analyst output: `supporting_facts` and `limiting_facts` arrays containing only
  verbatim supplied title/location/snippet facts, plus `centrality` equal to one
  concise sentence; no numeric score
- Analyst instruction distinguishes explicit core/repeated, direct but
  incidental, absent, and contradicted evidence
- Final scorer: fixed cp200, canonical system and original user message,
  temperature 0, plus a compact advisory analysis block
- Schema failure: unannotated original-prompt passthrough
- No label-to-score mapping, thresholds, case rules, or golden data

### Evaluation results

- Validation exact accuracy: 25/52 = 0.4808
- Validation numeric MAE: 0.6471
- Valid source-grounded analysis blocks: 38/52
- Canonical validation baseline: 33/52 = 0.6346 exact, 0.3529 numeric MAE
- Golden evaluation: not run
- Result: **REJECTED BEFORE GOLDEN**

### Improvement/regression

The advisory analysis lost eight exact validation cases and increased numeric
MAE by 0.2942. It systematically biased weak or missing evidence upward, often
turning expected 0--2 results into 2--4, and one expected N/A case became 4.
Only 38 responses passed the deliberately strict source-grounding validator.

### Conclusion and proposed next step

Reject without a golden run and do not add analyst prose to the production
prompt. Restore Attempt 8's direct Jina selection. Next test an extractive
selector over the complete atomic description, including title and location,
so the auxiliary model can improve evidence coverage without generating an
interpretation that anchors the scorer upward.

## Attempt 39: full-description Qwen2.5 3B extractive selector

Status: failed

### Strategy and rationale

Attempt 37 constrained the selector to the expanded-query BGE top 10, so a
reranker could not recover relevant centrality, scope, or contradiction facts
that dense retrieval had omitted. Give Qwen2.5 3B every deduplicated atomic
description fragment plus title and location context, while still requiring it
to return exactly two source indices. The auxiliary model cannot generate
evidence or a score; fixed cp200 sees only the two selected verbatim fragments.

### Exact configuration

- Availability: BGE-small raw-preference top 2, fixed cp200, canonical prompt,
  temperature 0
- Query expansion/fallback candidates: `qwen2.5:1.5b` Attempt 8 query,
  BGE-small top 10
- Primary selector: `qwen2.5:3b`, temperature 0, JSON, all deduplicated atomic
  source fragments from `generate_hybrid_chunks(..., window_size=1)`, title and
  location as context, exactly two distinct source indices
- Selector system prompt: `Select a jointly diagnostic set of verbatim
  job-description fragments for a separate preference scorer. Cover the
  clearest direct support or contradiction and, when possible, evidence showing
  whether the preference is a core or repeated role feature rather than an
  incidental mention. Prefer concrete duties, work conditions, requirements,
  and technologies over headings, generic employer language, and application
  boilerplate. Use the title and location only as context. You must select the
  requested number of distinct zero-based evidence indices; never abstain,
  score the job, rewrite evidence, or invent text. Return one JSON object with
  exactly one array field named selected_indices.`
- Selector user template: `Preference Guidance: {preference_guidance}\nJob
  Title: {job_title}\nJob Location: {job_location}\nSelect exactly
  {selection_count} indices.\nEvidence fragments:\n{index}: {verbatim
  fragment}...`
- Invalid-selector fallback: Attempt 8 Jina tiny raw-preference top 2 from the
  expanded-query BGE top 10
- Final scoring: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical system/user
  prompt, temperature 0, exactly two selected verbatim fragments; its score is
  the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-39 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, analyzer metadata, score combination, calibration,
  thresholds, case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 15/53 = 0.2830
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.1800
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-39/`

### Improvement/regression

The full-pool LLM selector lost nine exact cases and added 0.24 MAE relative to
Attempt 8, while preserving N/A behavior. Although every output followed the
index schema, the selector frequently chose broadly related scope fragments or
section headings instead of the most preference-diagnostic evidence. Its
instruction-following reliability did not translate into ranking quality.

### Conclusion and proposed next step

Reject and remove the LLM selector from the scoring path. Test exhaustive
cross-encoding over the same complete atomic pool: unlike the generative
selector, the strongest Jina tiny model is optimized specifically for
query-passage relevance and can be applied without dense candidate truncation.

## Attempt 40: exhaustive Jina cross-encoder reranking

Status: failed

### Strategy and rationale

Attempt 8's cross-encoder can only rank the ten fragments admitted by expanded
dense retrieval. Attempt 39 tested complete-pool recall with a generative model
but selected poor scope fragments. Apply the strongest Jina tiny relevance
model directly to every deduplicated atomic description fragment, removing the
dense candidate bottleneck while retaining a specialized non-generative
reranker and exactly two source passages.

### Exact configuration

- Availability: raw preference, `BAAI/bge-small-en-v1.5` top 2, canonical
  prompt, fixed cp200, temperature 0
- Final candidate pool: every deduplicated atomic fragment from
  `generate_hybrid_chunks(..., window_size=1)`; no generated query and no dense
  truncation
- Reranker: `jinaai/jina-reranker-v1-tiny-en`, raw preference, top 2
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical system/user
  prompt, temperature 0, two selected verbatim fragments; its score is the only
  returned score
- Error fallback: return the completed availability result
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-40 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, LLM selection, score fusion, calibration, thresholds,
  case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 21/53 = 0.3962
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0400
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-40/`

### Improvement/regression

Exhaustive cross-encoding lost three exact cases and added 0.10 MAE relative to
Attempt 8, while preserving N/A behavior. It improved materially over the
generative full-pool selector but still admitted description fragments that
expanded-query dense retrieval had usefully filtered. Candidate precision, not
just recall, is important for this compact reranker.

### Conclusion and proposed next step

Reject and restore Attempt 8's expanded-query BGE top-10 candidate stage. A
case-level comparison shows Attempt 8 and complete-description scoring have 31
correct cases in their oracle union but only 10 shared correct cases. Do not
select between their numeric outputs. Instead, give cp200 both focused and
global source evidence in one fresh scoring call so it performs the sole
adjudication.

## Attempt 41: focused retrieval plus complete source context

Status: failed

### Strategy and rationale

Focused Attempt 8 and full-description Attempt 24 expose complementary local
relevance and global role-scope signals. Combine the evidence views before any
numeric decision: present the two expanded-query/BGE/Jina verbatim passages
first, followed by the complete normalized source description as global
context. Fixed cp200 makes one fresh final score from both views; no candidate
scores are generated, selected, or combined.

### Exact configuration

- Availability: raw preference, `BAAI/bge-small-en-v1.5` top 2, canonical
  prompt, fixed cp200, temperature 0
- Query expansion: `qwen2.5:1.5b`, Attempt 8's exact JSON search-query prompt,
  temperature 0
- Candidate retrieval: BGE-small expanded-query top 10 atomic fragments
- Focused reranking: `jinaai/jina-reranker-v1-tiny-en`, raw preference, top 2
- Final evidence, in order: two reranked verbatim passages, then one source
  block equal to `Complete job description (global role context):\n{complete
  normalized description}`
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical system/user
  prompt, temperature 0; its single response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-41 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score fusion, view selection, generated evidence, calibration mapping,
  thresholds, case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 18/53 = 0.3396
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-41/`

### Improvement/regression

The combined prompt reduced MAE by 0.02 relative to focused Attempt 8 and by
0.08 relative to full-description Attempt 24, while preserving N/A behavior.
However, it lost six exact cases relative to Attempt 8 and gained only one exact
case over full context. Simply appending both views caused global boilerplate
to dilute decisive focused passages rather than letting cp200 preserve their
complementarity.

### Conclusion and proposed next step

Reject simultaneous focused/global context. Test adaptive RAG view routing with
Qwen2.5 3B: it sees both source views but must choose only `focused` or `global`
without assessing fit or emitting a score. The selected untouched evidence view
is then scored once by fixed cp200. Default to focused on any routing failure.

## Attempt 42: adaptive focused/global evidence routing

Status: failed

### Strategy and rationale

Attempt 8 and Attempt 24 have complementary correct cases, while concatenating
their evidence dilutes the focused view. Use an auxiliary instruction model as
an evidence-router, not a judge: choose focused context when explicit evidence
is decisive, or global context when overall balance, frequency, centrality, or
scope is necessary. The router never sees candidate scores and cannot emit a
fit assessment. Fixed cp200 makes the only numeric decision over one untouched
source view.

### Exact configuration

- Availability, query expansion, BGE-small expanded-query top 10, Jina tiny
  raw-preference top 2, canonical prompts, and temperature: identical to
  Attempt 8
- Router: `qwen2.5:3b`, temperature 0, JSON; input contains preference, title,
  location, two focused verbatim snippets, and complete normalized description
- Router system prompt: `Choose which source-evidence view is more reliable for
  a separate job-preference scorer. The focused view contains the passages with
  highest query relevance; the global view contains the complete job
  description. Choose focused when the title, location, or focused passages
  provide explicit decisive support or conflict and unrelated global text would
  dilute it. Choose global when the preference depends on the overall balance,
  frequency, centrality, hands-on scope, or limitations across duties and the
  focused passages alone could misrepresent that. Do not assess the fit, assign
  a score, summarize, or invent evidence. Return one JSON object with exactly
  one string field named evidence_view whose value is focused or global.`
- Router user template: `Preference Guidance: {preference}\nJob Title:
  {title}\nJob Location: {location}\n\nFocused view:\n- {snippet
  1}\n- {snippet 2}\n\nGlobal view:\n{complete normalized description}`
- Invalid/error fallback: focused evidence
- Final evidence: either the two focused passages or one complete-description
  source block, exactly as in Attempts 8 and 24
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0; its response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-42 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No candidate score generation, score fusion, deterministic score selection,
  calibration, case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9600
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-42/`

### Improvement/regression

Adaptive routing gained five exact cases relative to always-global Attempt 24
but remained one exact case and 0.02 MAE behind always-focused Attempt 8. The
router correctly switched several scope-sensitive coding/backend cases to full
context, but also switched decisive title/location mismatches and explicit
remote matches away from focused evidence. Valid schema adherence was not the
limitation; view utility for cp200 was too difficult to infer without scoring.

### Conclusion and proposed next step

Reject the router and restore direct focused scoring. Isolate query-generation
capacity by replacing the 1.5B expansion model with Qwen2.5 3B while keeping
BGE top-10 generation, Jina tiny reranking, and fixed cp200 scoring unchanged.

## Attempt 43: Qwen2.5 3B semantic query expansion

Status: failed

### Strategy and rationale

Attempt 8's 1.5B auxiliary model improves retrieval substantially but may omit
role synonyms, duty language, technologies, or contradiction terms from terse
preferences. Use the stronger 3B instruction model for the exact same
evidence-free semantic query task. It neither sees a job nor produces evidence
or a score; all downstream evidence remains verbatim and fixed cp200 remains
the sole scorer.

### Exact configuration

- Identical to Attempt 8 except query-expansion model `qwen2.5:3b`
- Expansion prompt, temperature 0, and JSON `search_query` schema: exactly
  Attempt 8's configuration
- Availability: BGE-small raw-preference top 2 and fixed cp200 canonical prompt
- Candidate retrieval: BGE-small 3B-expanded-query top 10 atomic fragments
- Reranking: Jina tiny raw-preference top 2
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0; its response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-43 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated job evidence, LLM selection, score fusion, calibration,
  thresholds, case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 16/53 = 0.3019
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.2200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-43/`

### Improvement/regression

The 3B expansion lost eight exact cases and added 0.28 MAE relative to the 1.5B
Attempt 8 query, while preserving N/A behavior. Its longer, broader semantic
queries admitted more loosely related fragments into the dense top 10; Jina
could not consistently recover the direct passages. More generator capacity did
not improve this tightly constrained retrieval task.

### Conclusion and proposed next step

Reject and restore Qwen2.5 1.5B. Test contrastive multi-query retrieval:
generate separate supporting and conflicting search queries, retrieve each
independently alongside the raw preference, deduplicate their small candidate
pools, and retain the proven Jina top-2/cp200 final path.

## Attempt 44: contrastive multi-query retrieval

Status: failed

### Strategy and rationale

A single query vector blends supporting and contradictory concepts, which can
hide decisive passages on either side. Generate separate semantic queries for
strong central support and explicit conflict/limitation, and retrieve each
independently together with the unmodified preference. This is standard
multi-query RAG: it improves recall without generating job evidence, and the
proven cross-encoder still makes the final passage ranking.

### Exact configuration

- Availability: raw preference, BGE-small top 2, fixed cp200 canonical prompt,
  temperature 0
- Query model: `qwen2.5:1.5b`, temperature 0, JSON
- Query system prompt: `Generate two concise semantic search queries for
  retrieving verbatim evidence about one candidate job preference. The support
  query should describe explicit job language that would strongly and centrally
  satisfy the preference. The conflict query should describe explicit opposite
  conditions, constraints, or role language showing that the preference is
  contradicted or only incidental. Preserve the preference's meaning and
  intensity. Include relevant role titles, duties, technologies, and synonyms
  when useful. Do not evaluate any job or assign a score. Return one JSON object
  with exactly two string fields: support_query and conflict_query.`
- Dense retrieval: BGE-small atomic top 5 independently for raw preference,
  `support_query`, and `conflict_query`; stable exact-text deduplication, at most
  15 candidates
- Reranking: Jina tiny against raw preference, top 2
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0, two selected verbatim passages; its response is the only
  returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-44 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, score fusion, calibration, deterministic scoring,
  preference-specific rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 18/53 = 0.3396
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0800
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-44/`

### Improvement/regression

Contrastive multi-query retrieval lost six exact cases and added 0.14 MAE
relative to Attempt 8, while preserving N/A behavior. Separating support and
conflict improved candidate recall but enlarged the pool with semantically
related distractors; raw-preference Jina scores did not reliably distinguish
the most diagnostic side of the evidence.

### Conclusion and proposed next step

Reject and restore Attempt 8. Its remaining confusion shows general ordinal
compression: clearly unrelated roles are often 1 instead of 0, while direct
central evidence is often 2--3 instead of 4--5. Test a balanced calibration
reminder on the fingerprint-disjoint validation split before any golden run.

## Attempt 45: balanced extreme-calibration reminder

Status: rejected on disjoint validation

### Strategy and rationale

Preserve the canonical system and evidence exactly, adding one short local
reminder that clarifies both ends of the existing rubric. Unlike Attempt 4's
upward-only reminder, explicitly distinguish boilerplate lexical overlap from
genuine weak fit and reserve 1--2 for real but weak/partial evidence. This is a
uniform semantic rubric clarification, not a score mapping or postprocessor.

### Exact configuration

- Validation: all 52 fingerprint-disjoint exported validation rows with their
  exact canonical system/user messages and source snippets
- Scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, temperature 0
- Text appended to the target user message:
  `Calibration check: Ignore mere lexical overlap in generic or boilerplate
  text. A clearly unrelated role is 0, not 1. Conversely, direct core or
  repeated support warrants 4 or 5, not 2. Reserve 1 and 2 for genuine but weak
  or partial fit.`
- No retrieval change, examples, label mapping, score postprocessing, case
  rules, or golden data

### Evaluation results

- Validation exact accuracy: 13/52 = 0.2500
- Validation numeric MAE: 0.8261
- Expected/predicted N/A counts: 1/5
- Canonical validation baseline: 33/52 = 0.6346 exact, 0.3529 numeric MAE
- Golden evaluation: not run
- Result: **REJECTED BEFORE GOLDEN**

### Improvement/regression

The balanced reminder lost 20 exact cases, added 0.4732 numeric MAE, and
introduced four extra N/A predictions. It shifted many expected 4--5 cases
downward even though the text explicitly encouraged strong scores. The
response-only checkpoint treats prompt suffixes as a substantial distribution
shift rather than a reliable rubric clarification.

### Conclusion and proposed next step

Reject without a golden run and preserve the canonical prompt exactly. Resume
preparation of Qwen2.5 7B and test it as a higher-capacity evidence router. The
router may inspect independently produced cp200 assessments but must output only
which untouched source view cp200 assessed more reliably; cp200 remains the
only numeric scorer.

## Attempt 46: score-aware focused/global assessment routing

Status: failed offline replay

### Strategy and rationale

Attempt 42 asked a 3B router to predict evidence-view utility without seeing how
cp200 interpreted either view. Replay the already generated, fixed cp200
Attempt 8 and Attempt 24 assessments and let a router choose which assessment
is better supported by the complete source. The router cannot create, average,
or modify a score; every selectable numeric value was emitted by the required
cp200 model. This first 3B replay tests the value of assessment-aware routing
while the higher-capacity 7B router downloads.

### Exact configuration

- Replay cases: all 53 canonical cases, using stored cp200 outputs from
  `cp200-attempt-08` (focused) and `cp200-attempt-24` (global)
- Focused evidence reconstruction: exact Attempt 8 Qwen2.5 1.5B expanded query,
  BGE-small top 10, Jina tiny raw-preference top 2
- Global evidence: complete normalized source description
- Router: `qwen2.5:3b`, temperature 0, JSON; expected labels, rationales, tags,
  and provenance are excluded from its input
- Router system prompt: `Choose which of two candidate assessments from the
  same fixed scoring model is better supported by the supplied job source. The
  focused assessment used two query-relevant verbatim passages; the global
  assessment used the complete description. Apply this ordinal contract when
  comparing them: 0 means explicit conflict, clearly unrelated, or unsupported;
  1 means genuine but tiny indirect overlap; 2 means partial fit that is not
  core; 3 means good direct fit; 4 means strong explicit fit; 5 means
  exceptional central and repeated fit. N/A means no meaningful evidence.
  Prefer focused when a title, location, or passage is explicit and decisive
  and global boilerplate would dilute it. Prefer global when overall frequency,
  balance, centrality, hands-on scope, or limitations are needed to check the
  focused assessment. Do not compute, average, change, or output any score.
  Return one JSON object with exactly one string field named selected_view whose
  value is focused or global.`
- Equal candidate assessments bypass routing because either view returns the
  identical cp200 value
- Selected result: the untouched stored cp200 assessment from the chosen view
- No new scoring model, arithmetic, label mapping, thresholds, case rules, or
  golden-derived router input

### Evaluation results

- Routed exact accuracy: 19/53 = 0.3585
- Routed N/A F1: 1.0
- Routed numeric MAE: 1.0600
- Route counts: focused 16, global 12, equal assessments 25
- Result: **FAILED OFFLINE REPLAY**

### Improvement/regression

Score-aware routing lost five exact cases and added 0.12 MAE relative to focused
Attempt 8. It improved by two exact cases over global Attempt 24 but remained far
below the 31-case oracle. The router repeatedly selected global 1 over focused 0
for clearly unrelated roles and chose focused underpredictions over correct
global scope judgments on several coding/backend cases.

### Conclusion and proposed next step

Reject the 3B router. Complete the partially downloaded 7B model, but use it
first in a held-out qualitative-metadata experiment rather than assuming that
more capacity alone fixes view routing. Preserve cp200 as the availability and
numeric scorer.

## Attempt 47: Qwen2.5 7B source-grounded qualitative metadata

Status: rejected on disjoint validation

### Strategy and rationale

Use the stronger auxiliary model for a bounded qualitative classification that
mirrors the established rubric but contains no number. Require a verbatim source
quote so invented classifications are discarded. Append valid advisory metadata
to the unchanged held-out prompt and let fixed cp200 produce the sole numeric
response. This tests whether stronger source reasoning can correct ordinal
compression without score mappings or generated job facts.

### Exact configuration

- Validation: all 52 fingerprint-disjoint exported rows with exact canonical
  title, location, two source snippets, and cp200 system prompt
- Analyst: `qwen2.5:7b`, temperature 0, JSON
- Allowed relationships: `opposed_or_unrelated`, `tiny_indirect`,
  `partial_noncore`, `good_direct`, `strong_explicit`, `central_repeated`, and
  `insufficient`
- Analyst system prompt: `Classify how supplied job evidence relates to one
  candidate preference for a separate scoring model. Use exactly one
  qualitative relationship: opposed_or_unrelated means explicit conflict, a
  clearly unrelated role, or meaningful evidence that does not support the
  preference; tiny_indirect means genuine but tiny indirect overlap;
  partial_noncore means a partial fit that is not a core responsibility;
  good_direct means good fit with direct evidence; strong_explicit means strong
  fit with explicit evidence; central_repeated means exceptional fit that is
  central and repeatedly supported; insufficient means there is no meaningful
  evidence either way. Distinguish an unrelated role from missing evidence.
  Treat title and location as primary evidence, and do not let generic
  boilerplate create overlap. Never assign or mention a numeric score. Return
  JSON with exactly two string fields: relationship and evidence_quote. The
  quote must be one verbatim substring from the supplied title, location, or
  snippets that best supports the relationship, or an empty string only for
  insufficient.`
- Validation: relationship must be in the closed vocabulary; a non-insufficient
  quote must occur verbatim in the source prompt; `insufficient` requires an
  empty quote. Invalid output leaves the scorer prompt unchanged.
- Valid advisory suffix:
  `Advisory source-grounded evidence metadata (verify against the supplied
  facts):\n- qualitative_relationship: {relationship}\n- evidence_quote:
  {verbatim quote}`
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical messages,
  temperature 0; its response is the only numeric result
- Offline diagnostic only: after both models respond, compare the qualitative
  relationship with the held-out label's corresponding rubric phrase. This
  mapping is never exposed to either model and never determines a returned
  score.
- No numeric auxiliary output, category-to-score mapping in the scorer path,
  score fusion, thresholds, examples, case rules, or golden data

### Evaluation results

- Validation exact accuracy: 23/52 = 0.4423
- Validation numeric MAE: 0.7451
- Expected/predicted N/A counts: 1/0
- Analyst qualitative exact agreement: 8/52
- Valid source-quote annotations: 44/52
- Relationship distribution among valid annotations: `good_direct` 15,
  `opposed_or_unrelated` 6, `partial_noncore` 17, `strong_explicit` 6
- Canonical validation baseline: 33/52 = 0.6346 exact, 0.3529 numeric MAE
- Golden evaluation: not run
- Result: **REJECTED BEFORE GOLDEN**

### Improvement/regression

The metadata path lost ten exact cases, increased MAE by 0.3922, and converted
the sole expected N/A to a numeric score. The analyst systematically
underclassified high-fit cases as partial/good and occasionally labeled
expected-4 evidence opposed/unrelated. cp200 followed those categories, so
higher model capacity did not repair qualitative rubric alignment.

### Conclusion and proposed next step

Reject without a golden run. Perform the previously bounded score-aware routing
replay with Qwen2.5 7B, using only stored cp200 assessments and source evidence.
This isolates whether the larger model can choose between existing assessments
more reliably than it can produce ordinal metadata.

## Attempt 48: Qwen2.5 7B score-aware assessment routing

Status: failed offline replay

### Strategy and rationale

Repeat Attempt 46's exact replay with the downloaded 7B router. This is a pure
capacity comparison: all cp200 assessments, focused/global source views,
prompts, schemas, and evaluation procedures remain identical. The router emits
only `focused` or `global`; it cannot create or alter the returned cp200 value.

### Exact configuration

- Identical to Attempt 46 except router model `qwen2.5:7b`
- Temperature 0, JSON `selected_view`, same exact router system/user templates
- Equal cp200 assessments bypass routing because either view returns the same
  value
- Expected labels and rationales are excluded from router input and used only
  after routing for offline metrics
- No live scorer change unless the replay materially exceeds focused Attempt 8
  and is capable of meeting the gate

### Evaluation results

- Routed exact accuracy: 22/53 = 0.4151
- Routed N/A F1: 1.0
- Routed numeric MAE: 0.9200
- Route counts: focused 12, global 16, equal assessments 25
- Result: **FAILED OFFLINE REPLAY**

### Improvement/regression

The 7B router gained three exact cases and reduced MAE by 0.14 relative to the
3B router, but remained two exact cases below focused Attempt 8. It recovered
several global coding/backend scope cases while still selecting global 1 or 2
over focused 0 on explicit mismatches and missing late global corrections.
Higher capacity did not make evidence-view utility reliable.

### Conclusion and proposed next step

Reject and abandon focused/global score routing. Restore Attempt 8. Address a
retrieval-structure loss instead: atomic bullet chunking discards section
headings, so reranking cannot distinguish responsibilities, requirements, and
benefits. Attach the nearest verbatim heading as source metadata only in final
candidate generation while preserving the proven raw availability gate.

## Attempt 49: heading-contextual candidate retrieval

Status: failed

### Strategy and rationale

Atomic sentence/bullet chunks lose their source section, which makes a required
skill look like a core duty and lets benefits boilerplate compete with role
responsibilities. Contextual retrieval is an established RAG technique: attach
the nearest source heading to each atomic evidence unit before embedding and
reranking. Preserve the original atomic availability gate, and keep the final
two-item prompt budget.

### Exact configuration

- Availability: unchanged Attempt 8 raw-preference BGE-small top 2 atomic
  source chunks, fixed cp200 canonical prompt, temperature 0
- Query expansion: unchanged Attempt 8 `qwen2.5:1.5b` JSON semantic query
- Candidate chunks: each atomic sentence/bullet prefixed with source metadata
  `Section: {nearest markdown/bold/plain heading}\nEvidence: {source unit}`;
  units without a heading remain unchanged; stable exact deduplication
- Heading recognition: Markdown `#` headings, standalone bold headings, and
  short colon-terminated lines; heading text and evidence text both come from
  the normalized source description
- Dense candidate retrieval: BGE-small expanded-query top 10
- Reranking: Jina tiny raw-preference top 2
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0; its response is the only score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-49 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated facts, auxiliary assessment, score fusion, calibration, case
  rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-49/`

### Improvement/regression

Relative to Attempt 8, heading-contextual chunks lost one exact case and
increased MAE by 0.06 while preserving perfect availability classification.
Useful headings sometimes clarified responsibilities and requirements, but
many descriptions had no explicit headings; the conservative fallback then
treated long introductory prose as context, and pseudo-headings occasionally
added distracting tokens. Overall the metadata did not improve final ranking.

### Conclusion and proposed next step

Reject and restore Attempt 8's atomic candidate chunks. Isolate dense candidate
recall with a stronger embedding model while preserving BGE-small for the
proven availability gate. Validate scorer-prompt changes only on the separate
fingerprint-disjoint holdout; retrieval-model changes require source
descriptions and therefore a bounded golden run with no case-specific tuning.

## Attempt 50: BGE-base isolated candidate retrieval

Status: failed

### Strategy and rationale

Use the higher-capacity `BAAI/bge-base-en-v1.5` model only for expanded-query
candidate recall. Preserve BGE-small for Attempt 8's availability call so a
candidate-model change cannot regress the proven N/A decision. BGE-base has a
larger representation than BGE-small while retaining the same English BGE
retrieval family and cosine-ranking interface; Jina tiny still decides the
final top two from ten candidates.

### Exact configuration

- Availability: Attempt 8 raw-preference `BAAI/bge-small-en-v1.5` top 2,
  required cp200 canonical prompt, temperature 0
- Query expansion: Attempt 8 `qwen2.5:1.5b`, temperature 0, JSON
  `search_query`, with the exact prompt recorded in Attempt 8
- Candidate chunks: unchanged atomic sentence/bullet units with stable exact
  deduplication
- Candidate embedding model: `BAAI/bge-base-en-v1.5`, expanded-query cosine
  top 10
- Reranking: `jinaai/jina-reranker-v1-tiny-en`, raw preference, top 2
- Final scoring: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, Attempt 8 canonical
  system/user prompt, temperature 0; its response is the only score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-50 CANDIDATE_EMBEDDING_MODEL=BAAI/bge-base-en-v1.5 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt changes, score fusion, calibration, generated evidence, metadata,
  case rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 21/53 = 0.3962
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-50/`

### Improvement/regression

BGE-base lost three exact cases and increased MAE by 0.08 relative to
Attempt 8 while preserving perfect N/A classification. The larger model changed
candidate coverage but Jina still received and sometimes selected broadly
related boilerplate or standalone headings rather than diagnostic duties.

### Conclusion and proposed next step

Reject and restore BGE-small for both dense stages. Filter formatting-only
headings from final candidate generation so the two-item evidence budget cannot
be consumed by section labels; preserve unfiltered raw retrieval for the proven
availability gate.

## Attempt 51: heading-only candidate filtering

Status: failed

### Strategy and rationale

Atomic chunking deliberately preserves source lines, but standalone headings
such as `**Requirements:**`, `### An overview of this role`, and `Position
Complexities:` contain no job claim. Dense retrieval and cross-encoding can
rank these labels highly because their vocabulary resembles the preference,
wasting one of only two final evidence slots. Remove formatting-only headings
from the final candidate corpus while keeping all substantive sentences and
bullets. Leave availability retrieval unchanged.

### Exact configuration

- Models, exact query-expansion prompt, exact scorer prompt, temperature,
  candidate depth, reranker, and fallbacks: identical to Attempt 8
- Availability: unfiltered raw-preference BGE-small top 2 and required cp200
- Candidate filtering: reject only an atomic chunk that is a Markdown `#`
  heading, an entire bold heading, or a colon-terminated line of at most 12
  whitespace-delimited words
- Candidate retrieval: BGE-small expanded-query cosine top 10 over remaining
  atomic chunks
- Reranking: Jina tiny raw-preference top 2
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0; its response is the only score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-51 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score fusion, calibration, category logic, generated evidence, case rules,
  or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 24/53 = 0.4528
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9600
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-51/`

### Improvement/regression

Filtering headings tied Attempt 8's 24 exact cases and preserved perfect N/A,
but increased MAE by 0.02. It recovered substantive evidence where a formatted
heading would otherwise rank, but those changes traded exact cases and did not
improve the aggregate. Some visually heading-like plain text also remained,
and broadening the heuristic would risk deleting short substantive claims.

### Conclusion and proposed next step

Reject and restore the exact Attempt 8 retrieval corpus. Test snippet-order
sensitivity on the fingerprint-disjoint validation set before changing golden
behavior; the response-only checkpoint may anchor on the final rather than the
first evidence item.

## Attempt 52: reverse-ranked evidence order

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Reverse the two canonical evidence snippets before scoring. Dense retrieval
returns relevance-descending evidence, but decoder recency can cause the second,
weaker snippet to dominate. Reversal is a fixed, label-independent robustness
test that keeps every fact and every prompt token except order.

### Exact configuration

- Validation: all 52 exported fingerprint-disjoint cases
- Model: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Messages: exact exported canonical system/user messages, except the two
  `Relevant Context Snippets` bullet lines are reversed
- All 52 prompts had exactly two bullets and were reversed
- Temperature 0; direct model response; no retrieval, prompt suffix, score
  fusion, mapping, calibration, category logic, case rules, or golden data
- Script: `/tmp/eval_cp200_reverse_snippets.py`

### Evaluation results

- Canonical validation baseline: 33/52 = 0.6346 exact, numeric MAE 0.3529
- Reversed order: 31/52 = 0.5962 exact, numeric MAE 0.4118
- Reversed prediction distribution: 1=5, 2=6, 3=16, 4=19, 5=6; no N/A
- The sole expected N/A became numeric 2
- Golden gate: not run because the disjoint validation result regressed

### Improvement/regression

Reversal lost two exact cases, increased numeric MAE by 0.0589, and lost N/A
recall. The canonical relevance-descending order is better aligned with this
checkpoint than putting the strongest retrieved passage last.

### Conclusion and proposed next step

Reject and preserve relevance-descending evidence. Return to retrieval planning:
generate the semantic query from preference plus job title and location so the
same general preference can retrieve job-specific supporting and conflicting
language without altering the scorer prompt.

## Attempt 53: title- and location-aware query expansion

Status: failed

### Strategy and rationale

Attempt 8 expands each preference independently of the job, so every posting
with the same preference receives the same generic retrieval query. Supply the
job title and location to the query planner so it can focus vocabulary on the
role under review and retrieve job-specific support or contradiction. Metadata
is used only for search planning; it is already independently present in the
unchanged scorer prompt.

### Exact configuration

- Availability, BGE-small candidate embedding, atomic chunks, top-10 depth,
  Jina tiny raw-preference top 2, final canonical prompt, fixed cp200 model,
  temperature, and fallbacks: identical to Attempt 8
- Query planner: `qwen2.5:1.5b`, temperature 0, Ollama JSON mode, cached by
  exact model/preference/title/location tuple
- Query-planner system prompt:

  ```text
  Rewrite a candidate job preference as one concise semantic search query for
  retrieving evidence from this job's description. Use the supplied job title
  and location only to focus terminology; do not infer unstated facts from them.
  Include relevant duties, technologies, synonyms, and both supporting and
  contradicting phrases when useful. Preserve the preference's meaning and
  intensity. Do not evaluate the job and do not assign a score. Return one JSON
  object with exactly one string field named search_query.
  ```

- Query-planner user prompt:

  ```text
  Preference Guidance: {preference_guidance}
  Job Title: {job_title}
  Job Location: {job_location}
  ```

- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`; its direct response is
  the only score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-53 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated job facts, score fusion, calibration, category logic,
  case-specific rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 19/53 = 0.3585
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.1200
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-53/`

### Improvement/regression

Job-aware expansion lost five exact cases and increased MAE by 0.18 relative to
Attempt 8 while preserving perfect availability classification. The 1.5B
planner frequently overfocused on title vocabulary, producing candidates that
repeated broad role language or headings instead of retrieving more diagnostic
job claims.

### Conclusion and proposed next step

Reject and restore Attempt 8's preference-only query expansion. Test the
installed Qwen2.5 7B as a strictly extractive selector over the stable top-10
candidate pool. Prior 1.5B and 3B selectors may have lacked the capacity to
jointly judge directness, contradiction, and centrality.

## Attempt 54: Qwen2.5 7B extractive evidence selector

Status: failed

### Strategy and rationale

Replace Jina tiny's independent passage ranking with a larger LLM selector that
can choose a jointly diagnostic pair. It receives only BGE's ten verbatim
candidates plus title/location context and must return two distinct indices.
Unlike generated summaries, the selector cannot alter evidence; unlike numeric
probes, it cannot anchor or combine scores.

### Exact configuration

- Availability, preference-only `qwen2.5:1.5b` expansion with Attempt 8's exact
  prompt, BGE-small expanded-query top 10, atomic chunks, final scorer prompt,
  required cp200 scorer, temperature, and availability fallback: Attempt 8
- Selector: `qwen2.5:7b`, temperature 0, Ollama JSON mode, exactly two distinct
  zero-based candidate indices; title/location are context only
- Selector system prompt: exact `select_scoring_snippets_with_llm` prompt in
  `ai_scorer.py`: select direct support/contradiction plus centrality evidence,
  prefer concrete duties/conditions/requirements/technologies, never score,
  rewrite, invent, or abstain
- Selector user fields: preference guidance, job title, job location, exact
  selection count, and all ten indexed verbatim candidates
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  selected two verbatim snippets, temperature 0; its response is the only score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-54 EVIDENCE_SELECTOR_MODEL=qwen2.5:7b make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, numeric metadata, score fusion, calibration, category
  logic, case-specific rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 19/53 = 0.3585
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9800
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-54/`

### Improvement/regression

The 7B selector gained three exact cases over the 3B top-10 selector but still
lost five cases and added 0.04 MAE relative to Jina tiny. It selected strong,
central-looking passages reliably in several cases, yet cp200 continued to
compress clearly supportive evidence into scores 2--4. Larger selector capacity
did not resolve either final calibration or evidence-pair utility.

### Conclusion and proposed next step

Reject and restore Jina tiny. Isolate candidate recall with Jina v2 small
embeddings, which use a different long-context representation than BGE while
keeping BGE-small and the exact scorer prompt for availability.

## Attempt 55: Jina v2 small isolated candidate embeddings

Status: failed

### Strategy and rationale

Use `jinaai/jina-embeddings-v2-small-en` only for expanded-query top-10
candidate recall. It is an English retrieval embedding with an 8K input limit
and a representation independent from both previously tested BGE and Nomic
families. Preserve the best cross-encoder, final evidence budget, and scorer,
and preserve BGE-small for availability.

### Exact configuration

- Availability: Attempt 8 raw-preference BGE-small top 2 and required cp200
- Query expansion, atomic chunks, candidate depth, Jina tiny raw-preference
  reranking top 2, final canonical prompt, temperature, and fallbacks: Attempt 8
- Candidate embedding model: `jinaai/jina-embeddings-v2-small-en`,
  expanded-query cosine top 10; no query/document prefix is required by the
  FastEmbed model metadata
- Final scoring model:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`; its response is the
  only score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-55 CANDIDATE_EMBEDDING_MODEL=jinaai/jina-embeddings-v2-small-en make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt changes, generated evidence, score fusion, calibration, category
  logic, case-specific rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 21/53 = 0.3962
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0600
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-55/`

### Improvement/regression

Jina embeddings lost three exact cases and increased MAE by 0.12 relative to
Attempt 8 while preserving perfect availability classification. Their top-10
pool changed substantially but still exposed Jina tiny to generic role prose
and headings, and final scores remained compressed.

### Conclusion and proposed next step

Reject and restore BGE-small candidates. Test the BGE retrieval family with its
standard asymmetric query instruction on the expanded query only; the current
implementation embeds queries and passages identically.

## Attempt 56: instructed BGE candidate query

Status: failed

### Strategy and rationale

BGE's retrieval usage supports prepending a natural-language instruction to
queries while leaving passages unprefixed. Apply the standard English BGE
search instruction to the expanded candidate query so its vector is explicitly
oriented toward relevant passages. Preserve unprefixed raw availability
retrieval and every downstream component.

### Exact configuration

- Availability, preference-only expansion model/prompt, BGE-small passage
  embeddings, atomic chunks, candidate depth, Jina tiny reranking, final scorer
  prompt/model, temperature, and fallbacks: Attempt 8
- Candidate query embedded as:
  `Represent this sentence for searching relevant passages: {expanded_query}`
- Candidate passages: unchanged, unprefixed atomic source chunks
- Candidate embedding model: `BAAI/bge-small-en-v1.5`, cosine top 10
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`; direct response only
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-56 CANDIDATE_QUERY_PREFIX='Represent this sentence for searching relevant passages: ' make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt change to the scorer, generated evidence, score fusion,
  calibration, category logic, case-specific rules, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 24/53 = 0.4528
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9400
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-56/`

### Improvement/regression

Aggregate metrics are identical to Attempt 8. The instructed query changed
some rankings, but gains and losses canceled exactly; availability and score
error were unchanged.

### Conclusion and proposed next step

Reject the prefix and keep the candidate query unprefixed. Retrieval-model and
query-format changes have now saturated below the gate, so inspect the target
model's packaging, chat template, and training/runtime prompt parity before
another retrieval experiment.

## Attempt 57: structured metadata anchor plus strongest source snippet

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Preserve the job's two primary structured fields in the final evidence budget
instead of allowing a semantic reranker to discard them. Use one compact,
source-grounded metadata item containing the exact job title and location, then
one strongest verbatim description chunk. This is a uniform contextual
retrieval strategy: it does not inspect preference keys, scores, or golden
labels. It addresses traces where work-arrangement retrieval returned unrelated
prose and where the reranker dropped the sentence that defined the role.

Select or reject the prompt shape first on the 52 fingerprint-disjoint training
validation rows. Each exported row already contains two retrieved snippets; use
the first (highest-ranked) source snippet so the validation ablation isolates
the metadata anchor.

### Exact configuration

- Validation set: `src/python/ai_scorer/training/data/export/val.jsonl`
- Scoring model: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- System prompt, user field order, labels, whitespace, temperature 0, and
  response parser: canonical training/runtime contract
- Final `Relevant Context Snippets` values:
  1. `Job Title: {exact_title}; Job Location: {exact_location}`
  2. the first original verbatim exported snippet
- Planned runtime if validation is acceptable: Attempt 8 availability and
  query-expansion/candidate retrieval, then final metadata anchor plus Jina
  tiny's top-1 verbatim chunk
- No generated facts, score fusion, calibration, preference/category branches,
  case-specific conditions, or golden-derived parameters

### Evaluation results

- Disjoint validation exact accuracy: 20/52 = 0.3846
- Disjoint validation numeric MAE: 0.7059 (36 total absolute error over 51
  numeric-comparable cases)
- Predicted distribution: 1=3, 2=13, 3=21, 4=11, 5=4, N/A=0
- Golden gate: not run because validation materially regressed

### Improvement/regression

Relative to the canonical validation baseline, the anchor lost 13 exact cases,
doubled numeric MAE, and removed the only expected N/A. Repeating title and
location inside the snippet block displaced useful evidence and compressed low
and unavailable labels toward the middle.

### Conclusion and proposed next step

Reject the metadata anchor and preserve structured fields only in their trained
positions. Live-model inspection also confirmed the packaged canonical system
prompt, identity prompt template, and temperature 0; the prior system-message
ablation already showed byte-identical predictions. Continue from Attempt 8
and test a stronger pretrained cross-encoder because evidence traces show that
high-quality passages reach the top-10 pool but the tiny reranker often ranks
generic prose above them.

## Attempt 58: MS MARCO MiniLM L12 cross-encoder

Status: failed

### Strategy and rationale

Replace only Attempt 8's tiny Jina cross-encoder with the deeper 12-layer MS
MARCO MiniLM reranker. Representative traces show that clearly diagnostic
source sentences already exist in the expanded-query top-10 pool but lose to
generic role prose at reranking. The L12 model is a standard English passage
reranker, has twice the transformer depth of the previously rejected L6
variant, and remains a bounded 0.12-GB dependency.

### Exact configuration

- Availability, canonical scorer messages, scoring model, temperature,
  query-expansion model and exact prompt, BGE-small candidate retrieval,
  top-10 depth, two-snippet final budget, parsing, and fallbacks: Attempt 8
- Cross-encoder reranker:
  `Xenova/ms-marco-MiniLM-L-12-v2` through FastEmbed ONNX
- Reranker query: original preference guidance
- Reranker documents: expanded-query BGE-small top-10 verbatim atomic chunks
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`; its direct response is
  the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-58 RERANKING_MODEL=Xenova/ms-marco-MiniLM-L-12-v2 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, score fusion, calibration, preference/category logic,
  case-specific conditions, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 17/53 = 0.3208
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0800
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-58/`

### Improvement/regression

The L12 reranker lost seven exact cases and increased MAE by 0.14 relative to
Attempt 8 while preserving the availability gate. It often selected passages
that were topically related yet less diagnostic of directness or centrality;
the extra depth did not correct the objective mismatch.

### Conclusion and proposed next step

Reject and restore Jina tiny. Test uniform multi-retriever evidence fusion: one
raw-preference dense result plus one expanded-query/Jina result. The two
retrieval views have complementary traces, and fixed per-view allocation
prevents either ranker from consuming the entire two-snippet budget.

## Attempt 59: raw-dense and reranked evidence fusion

Status: failed

### Strategy and rationale

Allocate the final two-snippet budget across complementary retrieval views:
the strongest raw-preference BGE result and the strongest expanded-query/Jina
cross-encoder result. Raw dense retrieval often preserves explicit technical
phrasing such as “design and build” that the cross-encoder drops, while the
reranked view delivered the large aggregate gain in Attempt 8. Deduplicate in
source order and fill any collision from the remaining results. This is a
fixed, score-independent rank-fusion policy applied to every numeric case.

### Exact configuration

- Availability stage and its early N/A return: Attempt 8 raw BGE-small top 2,
  required cp200, canonical prompt, temperature 0
- Raw view: raw-preference BGE-small top 2; contribute rank 1
- Reranked view: Attempt 8 exact qwen2.5:1.5b query expansion, expanded-query
  BGE-small top 10, `jinaai/jina-reranker-v1-tiny-en`; contribute rank 1
- Deduplication/fill: preserve raw rank 1 then reranked rank 1, omit exact text
  duplicates, then fill to two from remaining reranked and raw ranks
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0; its direct response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-59 EVIDENCE_FUSION_MODE=raw_and_reranked make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, score fusion, calibration, preference/category logic,
  case-specific conditions, or golden-derived settings

### Evaluation results

- Golden gate exact accuracy: 21/53 = 0.3962
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 1.0000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-59/`

### Improvement/regression

Fusion lost three exact cases and increased MAE by 0.06 relative to Attempt 8.
The raw view's top result was often generic too; fixed allocation displaced a
better second Jina result without reliably restoring the diagnostic raw result.

### Conclusion and proposed next step

Reject and restore both Jina-selected snippets. Address a separate observed
train/runtime metadata mismatch: exported training and disjoint validation use
canonical work-arrangement values, whereas live target locations contain free
text such as country-qualified remote strings. Test preference-blind LLM
normalization of location metadata before scoring.

## Attempt 60: preference-blind job-location normalization

Status: failed after passing disjoint validation

### Strategy and rationale

Normalize free-form job-location text into the same compact work-arrangement
vocabulary present in the fingerprint-disjoint training export: `remote`,
`hybrid`, `onsite`, or `unknown`. A small auxiliary LLM sees only the raw
location, never the preference, description, expected label, or scorer output.
Country-qualified remote locations remain remote while their eligibility detail
is intentionally omitted because the scorer evaluates work arrangement, not
geographic eligibility. Apply the normalized value uniformly to both the
availability and final canonical scorer calls.

This is metadata normalization rather than a score rule. Select or reject it on
all 52 disjoint validation rows before a golden run; those rows already use the
canonical vocabulary, so a sound normalizer should leave their evidence
contract stable.

### Exact configuration

- Validation set: `src/python/ai_scorer/training/data/export/val.jsonl`
- Normalization model: `qwen2.5:1.5b`, temperature 0, Ollama JSON mode, cached
  by model and exact raw location
- Normalizer system prompt:

  ```text
  Normalize one raw job-location value for a work-arrangement evaluator. Return exactly one of remote, hybrid, onsite, or unknown. Preserve the explicitly stated work arrangement. A remote role restricted to a country or region is still remote. Use unknown when the value is blank or does not state a work arrangement. Do not infer from any information outside the raw value. Return one JSON object with exactly one string field named normalized_location.
  ```
- Normalizer user prompt: `Raw Job Location: {raw_location}`
- Scoring model: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Scorer system/user structure, title, snippets, temperature 0, response parsing,
  availability gate, and Attempt 8 retrieval/reranking: unchanged; only the
  value after `Job Location:` is normalized
- Planned gate command if validation is acceptable: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-60 NORMALIZE_JOB_LOCATION=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score mapping/fusion, preference/category logic, case-specific conditions,
  or golden-derived parameters

### Evaluation results

- Disjoint validation exact accuracy: 33/52 = 0.6346
- Disjoint validation numeric MAE: 0.3529
- Predicted distribution: 0=1, 1=3, 2=7, 3=11, 4=24, 5=6, N/A=0
- Every aggregate metric and the output distribution match the canonical
  zero-shot validation baseline
- Golden gate exact accuracy: 25/53 = 0.4717
- Golden N/A precision/recall/F1: 1.0/1.0/1.0
- Golden mean absolute error: 0.8800
- Golden result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-60/`

### Improvement/regression

No validation improvement or regression. The canonical `remote`, `hybrid`, and
`onsite` values remained stable; normalizing the one blank value to `unknown`
did not change its deterministic score. On the golden set, two
country-qualified remote cases improved from 2 to 3, and one coding case
improved from 3 to its expected 4. No case regressed. Relative to Attempt 8,
exact accuracy gained one case and MAE improved by 0.06.

### Conclusion and proposed next step

Keep location normalization: it is the first transformation that preserved the
complete disjoint baseline and improved the golden result without a regression.
Test the semantically more explicit normalized remote value `fully remote` on
disjoint validation. Country-qualified remote metadata still lacks corroborating
description snippets, and the compact scorer currently treats `remote` alone as
only partial evidence in those cases.

## Attempt 61: explicit `fully remote` normalized metadata

Status: failed after passing disjoint validation

### Strategy and rationale

Retain Attempt 60's preference-blind location normalizer, but render its
`remote` category as `fully remote` in the scorer's `Job Location` field. This
expresses the work arrangement already stated by raw values such as `Remote`
and `Remote (Sweden)` without adding job facts, scoring metadata, or evidence
snippets. Hybrid, onsite, and unknown remain unchanged.

Select or reject the representation on all 52 fingerprint-disjoint validation
rows before a golden evaluation. Because 28 validation rows use the canonical
`remote` value across all ten unrelated preferences, this is a broad check
against preference-specific or target-specific behavior.

### Exact configuration

- Normalizer model, exact JSON prompt, caching, scorer, evidence retrieval,
  canonical scorer messages, temperature, and fallbacks: Attempt 60
- Normalizer categories: `remote`, `hybrid`, `onsite`, `unknown`
- Scorer location rendering: normalized `remote` becomes `fully remote`; all
  other categories are passed through unchanged
- Validation set: all 52 rows in
  `src/python/ai_scorer/training/data/export/val.jsonl`
- Planned gate command if validation is acceptable: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-61 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score mapping/fusion, preference/category-conditioned scoring path,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Disjoint validation exact accuracy: 33/52 = 0.6346
- Disjoint validation numeric MAE: 0.3529
- Predicted distribution: 0=1, 1=3, 2=7, 3=11, 4=24, 5=6, N/A=0
- Every aggregate metric and the output distribution match both the canonical
  zero-shot baseline and Attempt 60
- Golden gate exact accuracy: 25/53 = 0.4717
- Golden N/A precision/recall/F1: 1.0/1.0/1.0
- Golden mean absolute error: 0.8200
- Golden result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-61/`

### Improvement/regression

No validation improvement or regression across 52 cases, including 28 rows
whose normalized location changed from `remote` to `fully remote`. On golden,
two additional remote cases moved from 3 to their expected 5 and one moved from
3 to 4. A backend case shifted from its expected 3 to 4 and a coding case from
its expected 4 to 3, leaving exact accuracy tied with Attempt 60. Aggregate MAE
improved by 0.06.

### Conclusion and proposed next step

Keep the explicit representation as the lower-error candidate, but test a
balanced descriptive rendering for every normalizer category on disjoint
validation. Uniformly marking hybrid as partly onsite and unknown as unknown
may improve interpretation without privileging only remote metadata.

## Attempt 62: descriptive work-arrangement metadata

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Render each preference-blind normalized location category as a compact,
unambiguous natural-language work-arrangement value. Unlike Attempt 61, which
expanded only `remote`, this treats all categories symmetrically and makes the
onsite component of hybrid explicit without deciding whether that is a match;
the required scoring LLM still interprets it against arbitrary preference
guidance.

Select or reject the representation on all 52 fingerprint-disjoint validation
rows before a golden run.

### Exact configuration

- Location normalizer model, exact JSON prompt, four output categories, cache,
  scorer, evidence retrieval, canonical scorer messages, temperature, and
  fallback: Attempt 60
- Uniform scorer-field rendering:
  - `remote` -> `fully remote work arrangement`
  - `hybrid` -> `hybrid, partly onsite work arrangement`
  - `onsite` -> `onsite work arrangement`
  - `unknown` -> `work arrangement unknown`
- Validation set: all 52 rows in
  `src/python/ai_scorer/training/data/export/val.jsonl`
- Planned gate command if validation is acceptable: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-62 NORMALIZE_JOB_LOCATION=true DESCRIPTIVE_JOB_LOCATION=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score mapping/fusion, preference/category-conditioned scoring path,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Disjoint validation exact accuracy: 29/52 = 0.5577
- Disjoint validation numeric MAE: 0.4314
- Predicted distribution: 0=1, 1=3, 2=8, 3=11, 4=23, 5=6, N/A=0
- Golden gate: not run because validation regressed

### Improvement/regression

The descriptive vocabulary lost four exact cases and added four total absolute
numeric-error points relative to the canonical baseline. Expanding hybrid and
onsite metadata perturbed unrelated preference scores; symmetric phrasing did
not preserve the checkpoint's trained contract.

### Conclusion and proposed next step

Reject and retain Attempt 61's simpler `fully remote` rendering with the other
categories canonical. Move back to evidence ranking: use the required scoring
LLM itself as a pointwise passage reranker so candidate utility is aligned with
the final scorer rather than a generic relevance objective.

## Attempt 63: cp200 pointwise evidence reranking

Status: failed

### Strategy and rationale

Replace Jina tiny's generic relevance ranking with pointwise LLM reranking by
the required cp200 scorer. For each of the first six expanded-query BGE
candidates, call cp200 with the canonical title, normalized location,
preference, and that one verbatim candidate. Rank candidates by the returned
ordinal relevance judgment, preserving dense rank for ties, select the top two,
and make a separate canonical cp200 call for the final score. This aligns
retrieval utility with the trained scorer while keeping intermediate and final
job evidence source-verbatim.

The intermediate outputs only rank passages. They are not averaged, mapped,
thresholded, or returned as the final score; cp200's final two-snippet response
remains authoritative. Attempt 61's disjoint-validated metadata normalization
is retained.

### Exact configuration

- Availability: Attempt 8 raw BGE-small top 2 and cp200 canonical call; early
  return on N/A
- Location metadata: Attempt 61 qwen2.5:1.5b preference-blind normalization,
  with `remote` rendered `fully remote` and other categories canonical
- Candidate generation: Attempt 8 exact qwen2.5:1.5b query expansion,
  BGE-small expanded-query top 10; pointwise stage considers dense ranks 1--6
- Pointwise reranker: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical system/user
  prompt and temperature 0, one candidate snippet per request
- Passage ordering: numeric scores descending; N/A below numeric; stable dense
  rank breaks ties; top two distinct verbatim snippets
- Final scoring: separate required cp200 canonical call with the selected pair,
  temperature 0; this direct response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-63 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true SCORER_POINTWISE_RERANK=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, auxiliary numeric score, score fusion/mapping,
  preference/category branch, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Golden gate exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.7600
- Result: **FAILED** exact accuracy; MAE and N/A constraints passed
- Artifacts: `eval-results/experiments/cp200-attempt-63/`

### Improvement/regression

Relative to Attempt 61, pointwise reranking lost two exact cases but reduced MAE
by 0.06 to the gate limit. It corrected four prior errors and moved several
underpredictions closer, but lost six previously exact cases. In particular,
selecting directly from dense candidates sometimes raised clear unrelated or
onsite cases from 0 to 1 and overemphasized partial backend evidence.

### Conclusion and proposed next step

Retain pointwise scorer alignment but restore a relevance guard. First use Jina
tiny to shortlist four candidates, then let cp200 select two only within that
shortlist. Preserve Jina order among the selected passages so pointwise scores
control inclusion rather than adding an ordering cue to the final prompt.

## Attempt 64: Jina-to-cp200 cascade reranking

Status: failed

### Strategy and rationale

Use a two-stage reranking cascade. Jina tiny first narrows the expanded-query
BGE top 10 to four topically relevant verbatim passages. The required cp200
scorer then evaluates those four pointwise with its canonical one-snippet
prompt and selects the two highest judgments. Emit the selected set in original
Jina rank order and make an independent canonical final scoring call.

This combines Jina's strong aggregate relevance guard from Attempt 8 with the
lower-error scorer-aligned ranking from Attempt 63. On an exported two-snippet
input the cascade is selection-invariant and preserves the original order, so
it does not alter the disjoint validation prompt contract.

### Exact configuration

- Availability and Attempt 61 location normalization: unchanged
- Candidate generation: Attempt 8 qwen2.5:1.5b expansion and expanded-query
  BGE-small top 10
- Stage-one reranker: `jinaai/jina-reranker-v1-tiny-en`, original preference
  query, top 4 verbatim candidates
- Stage-two pointwise reranker: required cp200, canonical title/normalized
  location/preference, one shortlist passage, temperature 0; numeric descending
  selection with stable Jina rank tie-break
- Final evidence order: original Jina order among the two selected passages
- Final scorer: separate required cp200 canonical two-snippet call, temperature
  0; its direct response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-64 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true SCORER_POINTWISE_RERANK_CASCADE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, auxiliary numeric score, score fusion/mapping,
  preference/category branch, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Golden gate exact accuracy: 24/53 = 0.4528
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-64/`

### Improvement/regression

The cascade landed between its parents: one more exact case but 0.04 worse MAE
than full pointwise reranking, and one fewer exact case but 0.02 better MAE than
Attempt 61. Only six outputs changed from Attempt 61; two became exact, three
previously exact scores overpredicted by one, and one underprediction moved
closer. The relevance guard reduced drift but did not create enough gains.

### Conclusion and proposed next step

Reject pointwise reranking variants and restore Attempt 61's Jina pair. Test a
preference-blind role synopsis in the existing title field on disjoint
validation. Terse or scraped titles do not consistently expose role family and
central hands-on work, while adding preference-conditioned metadata previously
failed; the synopsis therefore sees no preference and assigns no relation.

## Attempt 65: preference-blind normalized role synopsis

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Use Qwen2.5 7B to compress the raw job title, location, and two source evidence
snippets into a concise factual role synopsis. Replace only the value in the
existing `Job Title` field; preserve the canonical system/user structure and
both verbatim snippets. The synopsis states the role family and central work but
cannot see the candidate preference, score, expected label, or golden data.

This tests whether metadata normalization can expose centrality to the compact
scorer without preference-conditioned advisory labels or synthetic evidence.
Select or reject it on all 52 fingerprint-disjoint validation rows before any
golden evaluation.

### Exact configuration

- Validation set: `src/python/ai_scorer/training/data/export/val.jsonl`
- Synopsis model: `qwen2.5:7b`, temperature 0, Ollama JSON mode
- Synopsis system prompt:

  ```text
  Create a concise factual role synopsis for preference-blind job metadata normalization. Use only the supplied raw title, location, and source evidence. In 6 to 18 words, state the role family and its central work. Preserve stated seniority and specialty. Do not evaluate any candidate preference, assign a score, mention fit, add negative claims, or invent facts. Do not include work arrangement unless it is central to the job itself. Return one JSON object with exactly one string field named normalized_role.
  ```
- Synopsis user fields: exact raw title, exact normalized location, and both
  verbatim exported snippets; no preference guidance
- Scorer title value: exact `normalized_role` output (raw title is replaced)
- Location rendering: Attempt 61 (`remote` -> `fully remote`, other canonical
  categories unchanged)
- Required scorer, system prompt, remaining user structure, snippets,
  temperature 0, and response parsing: canonical
- Planned runtime if validation is acceptable: generate the synopsis from raw
  title, Attempt 61 normalized location, and Attempt 8's final two verbatim
  snippets, then make the canonical final cp200 call
- No score mapping/fusion, preference/category branch, case-specific condition,
  or golden-derived parameter

### Evaluation results

- Disjoint validation exact accuracy: 32/52 = 0.6154
- Disjoint validation numeric MAE: 0.3725
- Predicted distribution: 0=1, 1=2, 2=8, 3=12, 4=22, 5=7, N/A=0
- Golden gate: not run because validation regressed

### Improvement/regression

Replacing the title improved the first 26-row half by one exact case and one
error point, but lost two exact cases and added two error points in the second
half. Aggregate validation therefore lost one exact case and increased total
numeric error by one. The synopsis was broadly stable but did not justify
discarding the raw source title.

### Conclusion and proposed next step

Reject title replacement. Make one bounded follow-up that preserves the exact
raw title and appends the same deterministic preference-blind synopsis. Cache
synopses by their exact source inputs so this ablation changes only how the
existing metadata field is rendered.

## Attempt 66: raw title plus preference-blind role synopsis

Status: rejected after first disjoint-validation half; golden gate not run

### Strategy and rationale

Preserve the exact source title and append the Qwen2.5 7B role synopsis after an
em dash in the same `Job Title` field. This retains seniority, specialty, and
clean source wording that Attempt 65 sometimes compressed while adding a short
preference-blind description of the central work. Both verbatim snippets and
all other canonical fields remain unchanged.

### Exact configuration

- Validation rows, synopsis model, exact synopsis system/user prompts,
  temperature, JSON parsing, location rendering, required scorer, canonical
  scorer messages, and snippets: Attempt 65
- Synopsis cache: keyed by exact raw title, normalized location, and the two
  exact source snippets; cached text is the deterministic Attempt 65 output
- Scorer title value:
  `{exact_raw_title} — {normalized_role}`
- Selection: all 52 fingerprint-disjoint validation rows; golden is run only if
  the representation preserves or improves the canonical validation baseline
- Planned runtime if accepted: append the preference-blind synopsis generated
  from the raw title, Attempt 61 location, and Attempt 8 final verbatim pair
- No score mapping/fusion, preference/category branch, case-specific condition,
  or golden-derived parameter

### Evaluation results

- First disjoint-validation half exact accuracy: 8/26 = 0.3077
- First-half numeric MAE: 0.8400
- First-half predicted distribution: 1=1, 2=5, 3=8, 4=9, 5=3, N/A=0
- Remaining 26 validation rows and golden gate: not run because the first-half
  regression was already decisive

### Improvement/regression

The raw-title-plus-synopsis field lost six exact cases and added ten numeric
error points in the first half alone relative to that half of the canonical
baseline. Repetition and added generated text shifted the checkpoint toward
middle/high scores much more strongly than title replacement.

### Conclusion and proposed next step

Reject and abandon generated title metadata. Restore the exact raw title and
Attempt 61 location normalization. Test token-level late-interaction reranking,
which can preserve exact diagnostic phrase matches without generating or
annotating evidence.

## Attempt 67: AnswerAI ColBERT late-interaction reranking

Status: failed

### Strategy and rationale

Replace Jina tiny with `answerdotai/answerai-colbert-small-v1` over Attempt 8's
expanded-query BGE top-10 pool. ColBERT encodes query and passage tokens
separately and ranks with MaxSim—the sum, over query tokens, of their maximum
similarity to any passage token. This retains fine-grained matches such as
`design`, `build`, `backend`, `platform`, `remote`, and explicit contradictory
phrasing that can be diluted by a single dense vector.

The model is a compact 0.13-GB English late-interaction retriever with an
Apache-2.0 license. It cannot generate evidence or scores; the required cp200
model remains the sole availability and ordinal scorer. Attempt 61's
disjoint-validated location normalization is retained.

### Exact configuration

- Availability, qwen2.5:1.5b query expansion and exact prompt, expanded-query
  BGE-small top-10 generation, two-snippet budget, canonical scorer calls,
  temperature, parsing, and fallbacks: Attempt 61/Attempt 8
- Location: qwen2.5:1.5b normalization; `remote` rendered `fully remote`, other
  categories canonical
- Late-interaction model:
  `answerdotai/answerai-colbert-small-v1` via FastEmbed ONNX
- Late-interaction query: original preference guidance through `query_embed`
- Documents: ten verbatim BGE candidates through `passage_embed`
- Score: standard ColBERT MaxSim sum; descending rank, stable BGE rank ties;
  top two verbatim passages
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`; direct response only
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-67 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true LATE_INTERACTION_RERANK_MODEL=answerdotai/answerai-colbert-small-v1 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, score mapping/fusion, preference/category branch,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Golden gate exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8800
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-67/`

### Improvement/regression

ColBERT lost three exact cases and increased MAE by 0.06 relative to Attempt 61.
Token-level matches changed the evidence pair substantially but still favored
topical terms over the role-level directness and centrality the scorer needs.

### Conclusion and proposed next step

Reject and restore Jina tiny. Test stronger grounded evidence refinement on the
disjoint validation set. Attempt 5 used a 1.5B model and frequently restated the
preference as evidence; constrain Qwen2.5 7B to two factual statements supported
only by supplied title, location, and source snippets, with no relation label or
score.

## Attempt 68: Qwen2.5 7B grounded evidence statements

Status: rejected after first disjoint-validation half; golden gate not run

### Strategy and rationale

Use Qwen2.5 7B as a query-focused evidence compressor. Given the preference,
raw title, normalized location, and two selected verbatim source snippets, it
returns exactly two concise factual job statements: one about role scope or
centrality and one containing the strongest concrete evidence relevant to the
preference. The auxiliary model cannot score, label the relationship, turn the
preference into a job fact, or claim an absence unless the source explicitly
states it. The required cp200 model receives those two statements in its trained
snippet positions and remains the sole ordinal scorer.

Select or reject on all 52 fingerprint-disjoint validation rows before golden.
Runtime would retain Attempt 61's raw-evidence availability gate, so generated
statements cannot override N/A.

### Exact configuration

- Validation set: `src/python/ai_scorer/training/data/export/val.jsonl`
- Evidence model: `qwen2.5:7b`, temperature 0, Ollama JSON mode
- Evidence-refinement system prompt:

  ```text
  Prepare evidence for a separate job-preference scorer. Using only the supplied job title, location, and source snippets, return exactly two concise factual job statements. The first should state the role scope or central work that is relevant to evaluating the preference. The second should state the strongest concrete relevant duty, requirement, technology, or work condition. Preserve qualifiers and explicit limitations. Do not assign a score, name a fit level, present the candidate preference as a job fact, invent details, or claim something is absent unless the source explicitly states that absence. Return one JSON object with exactly one array field named evidence containing exactly two non-empty strings.
  ```
- Evidence-refinement user fields: exact preference guidance, raw title,
  Attempt 61 normalized location, and the two exact exported source snippets
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical system/user
  structure and temperature 0; only the two snippet values are replaced
- Planned runtime if validation is acceptable: Attempt 61 availability, query
  expansion, BGE top 10, Jina top 2, then 7B statements and final cp200 call
- No auxiliary score or relation label, score mapping/fusion,
  preference/category branch, case-specific condition, or golden-derived
  parameter

### Evaluation results

- First disjoint-validation half exact accuracy: 9/26 = 0.3462
- First-half numeric MAE: 0.6800
- First-half predicted distribution: 1=3, 2=3, 3=7, 4=12, 5=1, N/A=0
- Remaining 26 validation rows and golden gate: not run because the first-half
  regression was decisive

### Improvement/regression

The grounded statements lost five exact cases and added six numeric-error points
in the first half relative to its canonical baseline. The stronger refiner
avoided obvious malformed output but still shifted the response-only checkpoint
toward scores 3--4 and removed the source phrasing it had learned.

### Conclusion and proposed next step

Reject and abandon generated evidence. Restore Attempt 61's exact verbatim Jina
pair. Test contextual cross-encoder reranking where structured title and
normalized location are included in the reranker query, not as candidate text
or added scorer metadata.

## Attempt 69: title/location-contextual Jina query

Status: failed

### Strategy and rationale

Keep expanded-query BGE candidate recall, but give Jina tiny the original
preference together with structured job title and normalized location when it
ranks each verbatim passage. Passage relevance depends not only on topical word
overlap but on whether a statement describes the central work of this specific
role. Title context can distinguish, for example, core platform-building duties
from generic developer/company language; location context can retain work
arrangement evidence. The scorer prompt and source passages remain unchanged.

### Exact configuration

- Availability, Attempt 61 preference-blind location normalization, query
  expansion, BGE-small top-10 candidates, atomic chunking, final scorer,
  canonical prompt, temperature, parsing, and fallbacks: unchanged
- Cross-encoder: `jinaai/jina-reranker-v1-tiny-en`
- Reranker query exactly:

  ```text
  Preference Guidance: {original_preference_guidance}
  Job Title: {exact_job_title}
  Job Location: {Attempt 61 normalized_location}
  ```
- Reranker documents: ten verbatim expanded-query BGE candidates; top two
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`; direct response only
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-69 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true RERANK_WITH_JOB_CONTEXT=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence/metadata beyond validated location normalization, score
  mapping/fusion, preference/category branch, case condition, or golden-derived
  parameter

### Evaluation results

- Golden gate exact accuracy: 19/53 = 0.3585
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9000
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-69/`

### Improvement/regression

Contextual reranking lost six exact cases and increased MAE by 0.08 relative to
Attempt 61. Title tokens dominated passage ranking and displaced preference-
diagnostic evidence; keeping metadata out of the candidate text did not prevent
the generic relevance objective from drifting.

### Conclusion and proposed next step

Reject and restore the original-preference Jina query. Address guidance-form
distribution shift more conservatively than Attempts 20--21: an instruction-
following normalizer must return already-complete guidance exactly unchanged
and only grammaticalize terse fragments without adding semantic content.

## Attempt 70: identity-preserving guidance grammaticalization

Status: rejected during identity probe; scorer validation and golden not run

### Strategy and rationale

Normalize only the linguistic form of terse preference fragments. Complete
first-person guidance in the training/disjoint-validation set must be returned
byte-for-byte unchanged. Terse text is rewritten into one complete first-person
preference while preserving its exact scope and intensity and adding no titles,
duties, technologies, synonyms, examples, qualifiers, exceptions, opposites,
or domain concepts. This targets prompt-form shift without broadening the
retrieval/scoring criterion as Attempts 20 and 21 did.

First inspect exact outputs for every unique disjoint-validation and canonical
guidance string. Then score all 52 disjoint rows; golden is run only if their
baseline is preserved or improved.

### Exact configuration

- Guidance model: `qwen2.5:7b`, temperature 0, Ollama JSON mode, cached by model
  and exact input guidance
- Normalizer system prompt:

  ```text
  Normalize the linguistic form of one candidate preference for a downstream evaluator. If the input already states a complete preference with its scope or qualifiers, return it byte-for-byte unchanged. If it is terse or fragmentary, rewrite it as one complete first-person preference while preserving exactly the stated meaning, scope, and intensity. Do not add role titles, duties, technologies, synonyms, examples, qualifiers, exceptions, opposite conditions, or domain concepts. Do not evaluate any job or assign a score. Return one JSON object with exactly one string field named normalized_guidance.
  ```
- User prompt: `Preference Guidance: {exact_input}`
- Scorer preference field: exact `normalized_guidance` output
- Location normalization, availability, query expansion/candidates, original-
  preference Jina top 2, required cp200 final scorer, canonical prompt,
  temperature, parsing, and fallbacks: Attempt 61
- Planned gate command if validation is acceptable: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-70 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job-conditioned guidance, score mapping/fusion, preference/category branch,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Unique disjoint guidance strings returned byte-identically: 2/10
- Unique canonical guidance strings returned byte-identically: 3/6
- Scorer validation and golden gate: not run because exact identity preservation
  failed its prerequisite

### Improvement/regression

Despite the explicit instruction, Qwen 7B paraphrased eight already-complete
disjoint strings, changing wording such as `provided` conditions and preference
verbs. The output therefore did not isolate grammatical fragments and would
repeat the semantic drift of Attempts 20--21.

### Conclusion and proposed next step

Reject without scoring. Separate fragment detection from rewriting: ask the LLM
for only a boolean, preserve original bytes in code when false, and invoke the
same constrained rewriter only when true. Probe all unique strings before any
scoring call.

## Attempt 71: LLM-gated fragment-only guidance normalization

Status: rejected during fragment-classifier probe; scorer/golden not run

### Strategy and rationale

Use Qwen2.5 7B first as a preference-form classifier, not a rewriter. It returns
`needs_rewrite=true` only when the text cannot stand alone as a candidate
preference because its subject/experiencer or referent is missing (for example,
`Prefers X` or ambiguous `It requires Y`). Complete declarative preferences are
preserved byte-for-byte by program logic. Only classified fragments are passed
to Attempt 70's constrained first-person grammaticalizer.

This is a general metadata-form decision with no job input, score, preference
category, or target label. The exact original guidance—not an LLM copy—is used
for every `false` decision.

### Exact configuration

- Classifier model: `qwen2.5:7b`, temperature 0, Ollama JSON mode, cached by
  model and exact guidance
- Classifier system prompt:

  ```text
  Decide whether one candidate-preference string requires grammatical rewriting before it can stand alone. Set needs_rewrite to true only when the text is fragmentary or has a missing or ambiguous subject, experiencer, or referent, as in "Prefers X", "Strong preference for X", or "It requires Y". Set it to false for any complete declarative preference, including constructions such as "X is a priority", "My focus is X", "I want X", or "X is a key interest". Judge linguistic completeness only; do not paraphrase, interpret the preference, evaluate a job, or assign a score. Return one JSON object with exactly one boolean field named needs_rewrite.
  ```
- Classifier user prompt: `Preference Guidance: {exact_input}`
- False path: exact input bytes are preserved; no rewrite call
- True path: Attempt 70 exact grammaticalizer prompt/output
- Remaining Attempt 61 location/retrieval/scorer configuration unchanged
- Selection stages: probe all 16 unique disjoint/canonical strings; if all ten
  disjoint strings classify false, run all 52 disjoint scorer rows; golden only
  if the baseline is preserved or improved
- No job-conditioned guidance, score mapping/fusion, preference/category branch,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Unique disjoint guidance classified `needs_rewrite=false`: 10/10
- Unique canonical guidance classified `needs_rewrite=false`: 6/6
- True classifications: 0/16, including the prompt's explicit fragment forms
- Scorer validation and golden gate: not run because the classifier made the
  normalization path a complete no-op

### Improvement/regression

The classifier preserved all disjoint strings but also failed to recognize any
canonical fragments. Proceeding would reproduce Attempt 61 exactly while adding
7B latency and a failure mode.

### Conclusion and proposed next step

Reject and do not add guidance normalization. Isolate another source-metadata
ambiguity: make only the onsite component of `hybrid` explicit while retaining
Attempt 61's validated remote rendering and canonical onsite/unknown values.

## Attempt 72: explicit partly-onsite hybrid metadata

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Render normalized `hybrid` as `hybrid (partly onsite)` in the existing job
location field. This states the ordinary work-arrangement meaning already
present in the source category without evaluating whether it matches a
preference. Keep `remote` as Attempt 61's `fully remote`, and keep `onsite` and
`unknown` unchanged. The change is preference-blind and applies uniformly.

Select or reject on all 52 disjoint validation rows; 20 of them use hybrid
locations across diverse preferences.

### Exact configuration

- qwen2.5:1.5b location normalizer and exact prompt, cache, availability,
  Attempt 8 retrieval/Jina reranking, required cp200 canonical calls,
  temperature, parsing, and fallbacks: Attempt 61
- Scorer location rendering:
  - `remote` -> `fully remote`
  - `hybrid` -> `hybrid (partly onsite)`
  - `onsite` -> `onsite`
  - `unknown` -> `unknown`
- Validation: all 52 rows in
  `src/python/ai_scorer/training/data/export/val.jsonl`
- Planned gate command if validation is acceptable: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-72 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true EXPLICIT_HYBRID_LOCATION=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score mapping/fusion, preference/category-conditioned scoring path,
  case-specific condition, or golden-derived parameter

### Evaluation results

- First 26 disjoint rows: 14/26 exact (0.5385), numeric MAE 0.4400,
  predicted distribution 0=1, 1=3, 2=7, 3=5, 4=8, 5=2
- Remaining 26 disjoint rows: 17/26 exact (0.6538), numeric MAE 0.3462,
  predicted distribution 3=7, 4=14, 5=5
- Combined disjoint exact accuracy: 31/52 (0.5962)
- Combined disjoint numeric MAE: 20/52 = 0.3846
- Golden gate: not run because the full disjoint result regressed

### Improvement/regression

Relative to the canonical validation baseline, explanatory hybrid rendering lost
two exact cases and added two absolute-error points. The first half happened to
match its baseline aggregates, but the full fingerprint-disjoint set exposed the
regression. The output distribution also shifted two predictions from 4 to 3
and one from 4 to 5.

### Conclusion and proposed next step

Reject. Keep normalized hybrid, onsite, and unknown values canonical. Test the
remaining evidence-budget choice that has not been isolated in the successful
Attempt 61 pipeline: one final reranked snippet instead of two. Select it on all
52 disjoint rows before any golden run.

## Attempt 73: one-snippet final evidence budget

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Reduce the final scorer's evidence budget from the two highest Jina-reranked
snippets to the single highest snippet. A single passage can reduce dilution by
adjacent but weakly relevant evidence and may make strong support or conflict
more decisive. This is a uniform retrieval parameter, independent of preference
key, category, score, job metadata, or golden labels.

Select or reject on all 52 fingerprint-disjoint validation rows before golden.
The validation transformation retains each row's first retrieved snippet and
removes its second, directly isolating the fine-tuned scorer's response to the
one-passage budget.

### Exact configuration

- Scoring LLM: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Temperature, canonical system/user messages, response parsing, availability
  behavior, qwen2.5:1.5b location normalizer and its exact prompt: Attempt 61
- Location rendering: remote -> `fully remote`; hybrid, onsite, and unknown
  remain canonical
- Evidence selection: retain only rank 1 of the two validation passages; planned
  runtime pipeline is qwen2.5:1.5b semantic query expansion, BGE-small top 10,
  Jina tiny cross-encoder reranking, top 1
- Validation script: `/tmp/eval_cp200_top1.py`
- No score aggregation/mapping, preference/category branch, case-specific
  condition, or golden-derived parameter

### Evaluation results

- First 26 rows: 14/26 exact (0.5385), numeric MAE 0.5600,
  predicted distribution 0=1, 1=6, 2=6, 3=8, 4=4, 5=1
- Remaining 26 rows: 10/26 exact (0.3846), numeric MAE 0.7692,
  predicted distribution 1=1, 2=4, 3=8, 4=9, 5=4
- Combined exact accuracy: 24/52 (0.4615)
- Combined numeric MAE: 34/51 = 0.6667
- Expected N/A predicted numeric, so validation N/A F1 fell from 1.0 to 0.0
- Golden gate: not run

### Improvement/regression

The one-passage budget lost nine exact cases, nearly doubled MAE, and removed
the only expected N/A. It preserved first-half exact count but added three error
points there, then lost nine exact cases in the second half. The second passage
is complementary rather than merely dilutive on this disjoint set.

### Conclusion and proposed next step

Reject and retain two final snippets. Retrieval and metadata variants now leave
a domain-calibration gap: cp200's training preferences are substantially more
detailed than short runtime preferences. Test fixed balanced few-shot anchors
drawn only from the fingerprint-disjoint training split, selected without
consulting validation or golden labels. This is prompt calibration, not score
post-processing.

## Attempt 74: balanced training-split few-shot calibration

Status: rejected after first disjoint-validation half; golden gate not run

### Strategy and rationale

Prepend one canonical training example for each response label 0--5 and N/A.
Balanced demonstrations can ground the whole ordinal scale while leaving the
required cp200 model responsible for the final answer. Anchors come only from
the training split; selection does not inspect validation or golden examples.

Use the shortest prompt for each exact label, breaking ties by `case_id`. This
is a deterministic and reproducible context-length policy, not output
post-processing. Select or reject on all 52 fingerprint-disjoint validation
rows before golden.

### Exact configuration

- Scoring LLM: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Temperature 0; canonical system prompt from Attempt 1; canonical response
  parser; qwen2.5:1.5b location normalizer with Attempt 60's exact prompt;
  remote rendered `fully remote`, all other normalized categories canonical
- Runtime retrieval if selected: Attempt 61 expansion/BGE top-10/Jina top-2
- Message order: canonical system; seven user/assistant anchor pairs in label
  order 0,1,2,3,4,5,N/A; current canonical user prompt
- Anchor source:
  `src/python/ai_scorer/training/data/export/train.jsonl`
- Exact anchor case IDs in order:
  `5c3bbdcb-b2df-4f72-9f01-058fefc60462`,
  `c617b76b-759a-4059-89e5-ca53eaf92711`,
  `1cb52fd7-2225-4e5d-98bc-dc62d6cbdc76`,
  `89755c93-da29-47cb-8c5f-9f3ed3b8338a`,
  `2f2fcaa9-d84d-596a-a82d-0549d4fd90fb`,
  `4f0489a3-a05c-55f5-82c3-3af4e0b2fddf`,
  `23d8f89e-83fc-4632-8dfe-f7a5f9116e88`
- Exact anchor user prompts and assistant responses are the byte-exact
  `messages[-2:]` of those committed rows; selection script:
  `/tmp/eval_cp200_balanced_fewshot.py`
- No score aggregation/mapping, preference/category branch, case-specific
  condition, or golden-derived parameter

### Evaluation results

- First 26 disjoint rows: 5/26 exact (0.1923), numeric MAE 1.1600
- Predicted distribution: 1=2, 2=8, 3=6, 4=10
- Remaining validation rows and golden gate: not run because the preregistered
  acceptance criterion was already unattainable

### Improvement/regression

Compared with the canonical first-half baseline (14/26 exact, 0.4400 MAE), the
balanced history lost nine exact cases and added 18 error points. It also
suppressed both extremes, producing no 0 or 5. The cp200 fine-tune does not use
multi-example chat history as ordinal calibration.

### Conclusion and proposed next step

Reject. Preserve the byte-canonical single-query message structure. Return to
evidence ranking and inspect locally supported dedicated cross-encoders for a
stronger general relevance model; do not alter scorer messages.

## Attempt 75: raw-plus-expanded reciprocal-rank candidate fusion

Status: failed

### Strategy and rationale

Retrieve two complementary BGE-small candidate lists: one from the byte-exact
preference and one from qwen2.5:1.5b's semantic expansion. Fuse their ranks with
reciprocal rank fusion (RRF), cap the pool at ten, and let the successful Jina
cross-encoder choose the final two. Raw queries retain exact technologies and
constraints; expanded queries improve synonym and role-duty recall. RRF rewards
agreement without comparing incompatible score scales and without reserving a
final slot for either view (the weakness of Attempt 59).

The fingerprint-disjoint scorer validation cannot rerun candidate generation
because its committed rows contain only the already-selected passages, not the
source descriptions. The final prompt, location representation, scorer, and
two-passage budget are byte-identical to Attempt 61, whose 33/52 validation
result was already verified. This attempt changes only source-passage ranking.

### Exact configuration

- Scoring LLM: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Availability, qwen2.5:1.5b location normalization with exact Attempt 60
  prompt, remote -> `fully remote`, canonical scorer prompt, temperature 0,
  parsing, and fallback: Attempt 61
- Query expansion: qwen2.5:1.5b, temperature 0, JSON, exact Attempt 8 prompt
- Candidate embedding: `BAAI/bge-small-en-v1.5`; atomic chunks; raw top 10 and
  expanded-query top 10
- Candidate fusion: RRF score `sum(1 / (60 + rank))`, stable first-seen tie
  break, top 10
- Reranker: `jinaai/jina-reranker-v1-tiny-en` against raw preference, top 2
- Final scorer: separate required cp200 canonical call; no generated text enters
  the evidence
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-75 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true CANDIDATE_RETRIEVAL_MODE=raw_expanded_rrf make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score aggregation/mapping, preference/category branch, case-specific
  condition, or golden-derived parameter

### Evaluation results

- Golden exact accuracy: 20/53 = 0.3774
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9400
- Predicted distribution: 0=5, 1=11, 2=8, 3=11, 4=6, 5=9, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-75/`

### Improvement/regression

Relative to Attempt 61, RRF lost five exact cases and added 0.12 MAE while
preserving N/A. Raw/expanded rank agreement promoted generic passages; the
cross-encoder could not consistently recover Attempt 61's better candidates
after RRF capped the pool.

### Conclusion and proposed next step

Reject and restore expanded-query BGE candidates only. Probe the installed
domain-trained chat-full cp200 sibling as an extractive evidence selector, while
retaining the required response-cp200 model as the only final scorer.

## Attempt 76: domain-trained compact extractive selector

Status: rejected during live selector-contract check; gate aborted

### Strategy and rationale

Use the sibling chat-full cp200 checkpoint only to choose two verbatim passages
from Attempt 61's expanded-query BGE top 10. It was trained on the same scoring
domain and may recognize diagnostic duties better than generic rerankers, but it
does not score, rewrite, or generate evidence. The required response-cp200 model
then makes a separate canonical final scoring call.

A synthetic prerequisite probe selected the two correct diagnostic indices
`[1, 3]` from four backend/noise passages using the compact JSON contract. The
probe contained no evaluation job, preference, or label.

### Exact configuration

- Final scoring LLM and availability gate:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical messages,
  temperature 0, exact Attempt 61 behavior
- Location normalization/rendering: Attempt 61
- Expansion/candidates: qwen2.5:1.5b exact Attempt 8 expansion prompt;
  `BAAI/bge-small-en-v1.5` atomic top 10
- Selector model:
  `ai-scorer-qwen25:fp-v2-balanced-chat-full-cp200-f16`, temperature 0,
  Ollama JSON mode
- Exact selector system prompt:

  ```text
  Choose exactly 2 job-evidence fragments most diagnostic of whether the candidate preference is satisfied. Prefer explicit central support or contradiction over vague, generic, or administrative text. Indices are zero-based. Do not score the job, rewrite evidence, or invent text. Return JSON only with one field: selected_indices.
  ```

- Exact selector user template:

  ```text
  Preference: {preference_guidance}
  Job title: {normalized_job_title}
  Job location: {normalized_job_location}
  Evidence fragments:
  0: {candidate_0}
  ...
  9: {candidate_9}
  ```

- Final evidence: exact source strings at the two returned distinct zero-based
  indices, in selector-returned order
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-76 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true EVIDENCE_SELECTION_MODE=compact_llm EVIDENCE_SELECTOR_MODEL=ai-scorer-qwen25:fp-v2-balanced-chat-full-cp200-f16 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score aggregation/mapping, preference/category branch, case-specific
  condition, or golden-derived parameter

### Evaluation results

- Synthetic four-passage prerequisite: returned valid JSON indices `[1, 3]`
- Synthetic ten-passage exact production prompt: deterministically returned
  `[selected_indices]\n[1, 8]`, not JSON, on 3/3 calls
- Live gate: selector parsing failed on 3/3 attempted numeric cases among the
  first four fixture rows; one no-description case bypassed selection
- Full scorer metrics: not produced; run was intentionally interrupted at case
  5/53 rather than evaluating fallback behavior

### Improvement/regression

The checkpoint recognized good synthetic evidence indices, but did not honor
the declared JSON protocol under realistic ten-passage context. It emitted a
checkpoint-specific bracketed format and sometimes empty content. This is not a
reliable production reranker contract, and fallback-to-availability results
would not isolate its retrieval quality.

### Conclusion and proposed next step

Reject without changing Attempt 61. Avoid generated selector protocols. Test a
domain-trained pointwise reranker whose contract is the already reliable
canonical numeric scorer prompt: use the earlier balanced-response cp100
checkpoint to rank each passage, then keep the required cp200 checkpoint as the
separate and only final scorer.

## Attempt 77: cp100 domain pointwise passage reranker

Status: rejected on disjoint helper validation; golden reranking not run

### Strategy and rationale

Score each of the first six expanded-query BGE candidates independently with
the canonical scorer prompt using the same balanced-response training run's
cp100 checkpoint. Rank passages by that pointwise response and give the two
highest-scoring verbatim passages to the required cp200 final scorer. This uses
a reliable integer/N/A contract and domain-trained relevance signal without
making cp100's job score the returned score.

Cp100 is selected as an earlier checkpoint that may retain broader base-model
relevance judgment before cp200's stronger specialization. Before any golden
run, score all 52 fingerprint-disjoint validation rows directly with cp100's
canonical prompt. Proceed only if that independent result supports using it as
a passage judge. N/A ranks below numeric evidence during reranking.

### Exact configuration

- Availability and final scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Pointwise passage model:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp100-f16`
- All pointwise and final calls: canonical Attempt 1 system/user prompt,
  temperature 0, canonical parser
- Location normalization/rendering, availability, query expansion model and
  exact prompt, BGE-small atomic top-10 candidates: Attempt 61
- Pointwise candidates: first six dense candidates; one canonical call per
  passage; numeric descending, N/A below numeric, stable dense-rank tie break;
  top two selected
- Final call: separate cp200 canonical call with selected source passages
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-77 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true SCORER_POINTWISE_RERANK=true POINTWISE_RERANKER_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp100-f16 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score aggregation/mapping, preference/category branch, case-specific
  condition, or golden-derived parameter

### Evaluation results

- First 26 disjoint rows: 16/26 exact (0.6154), numeric MAE 0.4400,
  predicted distribution 0=1, 1=4, 2=5, 3=7, 4=8, 5=1
- Remaining 26 rows: 13/26 exact (0.5000), numeric MAE 0.5385,
  predicted distribution 2=3, 3=10, 4=10, 5=3
- Combined direct cp100 exact accuracy: 29/52 = 0.5577
- Combined numeric MAE: 25/51 = 0.4902
- The expected N/A was predicted numeric, so N/A F1 was 0.0
- Pointwise golden run: not run because the helper prerequisite regressed

### Improvement/regression

Cp100 improved first-half exact count by two with equal MAE, but lost six exact
cases in the second half. Overall it lost four exact cases, added seven numeric
error points, and missed N/A relative to cp200's disjoint baseline. The earlier
checkpoint is not independently reliable enough to promote as a reranker.

### Conclusion and proposed next step

Reject before golden. Systematically validate the remaining cp150, cp250, and
cp300 checkpoints under the identical prompt instead of choosing another
helper from golden behavior. Only a checkpoint matching or beating cp200 on the
full fingerprint-disjoint set is eligible for a pointwise reranking gate.

## Attempt 78: disjoint checkpoint helper sweep

Status: rejected on disjoint validation; no golden reranking run

### Strategy and rationale

Evaluate the remaining balanced-response checkpoints cp150, cp250, and cp300
directly on all 52 fingerprint-disjoint rows using the exact canonical prompt.
This is a preregistered helper-model selection sweep; it does not change or
evaluate the golden scorer pipeline. A checkpoint is eligible for a later
pointwise passage-ranking attempt only if it matches or improves cp200's 33/52
exact, 0.3529 MAE, and N/A behavior.

### Exact configuration

- Candidate helper models, evaluated in order:
  - `ai-scorer-qwen25:fp-v2-balanced-response-cp150-f16`
  - `ai-scorer-qwen25:fp-v2-balanced-response-cp250-f16`
  - `ai-scorer-qwen25:fp-v2-balanced-response-cp300-f16`
- Dataset: all 52 rows in
  `src/python/ai_scorer/training/data/export/val.jsonl`
- Canonical system and user messages, temperature 0, canonical parser
- qwen2.5:1.5b location normalizer with Attempt 60's exact prompt; remote ->
  `fully remote`, other normalized categories canonical
- Validation script: `/tmp/eval_balanced_checkpoints.py`
- Eligibility is aggregate and preregistered; no golden labels, per-case routes,
  score mapping, or category-specific conditions

### Evaluation results

- Cp150: 26/52 exact (0.5000), numeric MAE 0.5098, N/A exact 0/1;
  distribution 0=1, 1=2, 2=5, 3=22, 4=14, 5=8
- Cp250: 33/52 exact (0.6346), numeric MAE 0.3725, N/A exact 0/1;
  distribution 0=1, 1=2, 2=7, 3=12, 4=23, 5=7
- Cp300: 33/52 exact (0.6346), numeric MAE 0.3725, N/A exact 0/1;
  distribution 0=1, 1=2, 2=5, 3=12, 4=23, 5=9
- Cp200 reference: 33/52 exact, numeric MAE 0.3529, N/A exact 1/1

### Improvement/regression

Cp150 regressed heavily. Cp250 and cp300 matched cp200 exact count but each
added one numeric error point and missed the expected N/A. Therefore none met
the preregistered aggregate eligibility rule. Later checkpoints did not provide
an independently stronger helper contract.

### Conclusion and proposed next step

Reject all three as alternate pointwise models. Retain cp200 for passage
judgment, but improve its pointwise ranking resolution without changing its
greedy final scoring: rank candidates by the continuous expected first-token
ordinal value from cp200 log probabilities. This can break the many integer
ties observed in Attempt 63 using the same trained judgment.

## Attempt 79: log-probability-resolved cp200 pointwise reranking

Status: failed

### Strategy and rationale

Attempt 63 ranked six candidate passages by cp200's greedy integer score, which
creates many arbitrary dense-order ties. For each one-passage canonical call,
request the top 20 first-token log probabilities, normalize probability mass
over score tokens 0--5, and rank by the continuous expected ordinal value. Keep
N/A below numeric passages. The two selected verbatim passages then go to an
independent ordinary cp200 final call whose greedy response is returned.

This is scorer-based continuous passage reranking, not output calibration: no
expected value is returned as a job score, no threshold is fitted, and no
golden label or case metadata participates.

### Exact configuration

- Availability, pointwise passage judge, and final scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Location normalization/rendering, availability gate, qwen2.5:1.5b exact query
  expansion, BGE-small atomic top-10 candidates: Attempt 61
- Pointwise pool: first six dense candidates
- Pointwise API: `/api/chat`, canonical system/user prompt with one verbatim
  passage, temperature 0, `logprobs=true`, `top_logprobs=20`
- Passage rank value:
  `sum(s * exp(logprob_s), s=0..5) / sum(exp(logprob_s), s=0..5)`;
  explicit greedy N/A -> -1; stable dense-rank tie break; top two selected
- Final call: ordinary canonical cp200 two-passage call, temperature 0; its
  direct integer/N/A response is the only returned score
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-79 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true SCORER_POINTWISE_RERANK=true POINTWISE_USE_LOGPROBS=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score mapping/aggregation, preference/category branch, case-specific
  condition, or fitted/golden-derived parameter

### Evaluation results

- Golden exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8200
- Predicted distribution: 0=2, 1=9, 2=10, 3=11, 4=5, 5=13, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-79/`

### Improvement/regression

The probability signal changed selected pairs and increased predicted 5s from
three in Attempt 61 to thirteen, but it lost two exact cases and left MAE equal
to Attempt 61. Relative to integer pointwise Attempt 63, exact count was
unchanged and MAE regressed by 0.06. Breaking same-integer ties did not solve the
support-only ranker's tendency to omit decisive conflict.

### Conclusion and proposed next step

Reject. Keep the same continuous cp200 passage judgments but rank by diagnostic
extremity—distance from the rubric midpoint 2.5—so explicit support and explicit
contradiction can both outrank neutral/noisy passages. The final cp200 call
still interprets the selected source evidence and returns the only job score.

## Attempt 80: continuous diagnostic-extremity passage reranking

Status: failed

### Strategy and rationale

Use Attempt 79's continuous cp200 one-passage expectations, but rank passage
diagnosticity as `abs(expected_ordinal - 2.5)`. Scores near either rubric
extreme indicate decisive support or contradiction; scores near the midpoint
are ambiguous/partial. Explicit N/A remains below all numeric passages. Select
the two most diagnostic verbatim passages and make an independent canonical
cp200 final call.

This is a standard margin-from-neutral relevance criterion for evidence
selection. The midpoint is fixed by the declared 0--5 rubric, not fitted to
golden outcomes. It applies uniformly to every preference and job and never
changes or combines the final score.

### Exact configuration

- Models, metadata, availability, expanded-query BGE candidates, pointwise
  `/api/chat` log-probability calls, ordinal expectation formula, final cp200
  call, prompts, and temperature: Attempt 79
- Pointwise pool: first six expanded-query BGE candidates
- Diagnostic rank: numeric `abs(expected_ordinal - 2.5)` descending; explicit
  N/A rank -1; stable dense-order tie break; top two source passages
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-80 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true SCORER_POINTWISE_RERANK=true POINTWISE_USE_LOGPROBS=true POINTWISE_RANK_MODE=extremity make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No returned-score mapping/aggregation, preference/category branch,
  case-specific condition, or fitted/golden-derived parameter

### Evaluation results

- Golden exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9000
- Predicted distribution: 0=5, 1=11, 2=10, 3=7, 4=4, 5=13, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-80/`

### Improvement/regression

Diagnostic ranking lost three exact cases and added 0.08 MAE relative to Attempt
61; it also lost one exact and added 0.08 MAE relative to support-only Attempt
79. Selecting contradiction-like low-score passages amplified weak/noisy
evidence and reduced mid/high accuracy rather than clarifying mismatch.

### Conclusion and proposed next step

Reject and restore Attempt 61. Move from saturated evidence variants to the
remaining source-metadata mismatch: preference-blind conservative cleanup of
scraped title suffixes such as relative posting age and remote badges. First
require byte identity on every clean fingerprint-disjoint title.

## Attempt 81: conservative scraped-title normalization

Status: rejected during title-identity probe; scorer/golden not run

### Strategy and rationale

Normalize only the raw job-title field with qwen2.5:1.5b before scoring. Remove
obvious appended scrape/UI metadata—relative posting age, remote/hybrid badges,
location, salary, or feed status—while preserving the actual role title,
seniority, specialization, punctuation, and wording. The normalizer sees only
one raw title, never preference, description, score, or job ID.

The canonical runtime set contains title strings with appended feed text such
as `2 days ago Fully Remote`; the training and disjoint validation titles are
clean. Require byte-exact identity on every unique disjoint title before any
scoring evaluation. If clean identity holds, score all 52 disjoint rows and
proceed to golden only if their baseline is preserved or improved.

### Exact configuration

- Title normalizer model: `qwen2.5:1.5b`, temperature 0, Ollama JSON mode
- Exact system prompt:

  ```text
  Extract the job role title from one raw job-title value. Remove only obvious appended scraping or job-feed metadata such as relative posting age (for example "2 days ago"), work-arrangement badges (for example "Fully Remote" or "Hybrid"), geographic location, salary, or application status. Preserve the role title's original wording, seniority, specialization, punctuation, abbreviations, and capitalization byte-for-byte whenever no such appended metadata is present. Do not paraphrase, expand abbreviations, classify the role, or use outside information. Return one JSON object with exactly one string field named normalized_title.
  ```

- Exact user template: `Raw Job Title: {raw_title}\n`
- Identity probe: every unique title in the 52-row fingerprint-disjoint
  validation set; canonical titles are also inspected, but not used to tune the
  prompt after results
- Remaining models, prompts, retrieval, location normalization and rendering,
  two-snippet budget, temperature, and parsing if promoted: Attempt 61
- Probe script: `/tmp/probe_title_normalizer.py`
- No preference/category branch, score logic, case-specific condition, or
  golden label/rationale use

### Evaluation results

- Unique fingerprint-disjoint titles: 27
- Byte-identical disjoint outputs: 23/27
- Changed clean disjoint titles:
  - `Senior Engineering Manager - .NET, AWS, AI` ->
    `Senior Engineering Manager`
  - `Senior Manager, Software Engineering (Online Storage)` ->
    `Senior Manager, Software Engineering`
  - `Staff Engineer, AI Productivity a month ago New York, New York, United States · Fully Remote ·`
    -> `Staff Engineer, AI Productivity`
  - `Technical Lead – Full Stack (Java + React/Angular)` ->
    `Technical Lead - Full Stack (Java + React/Angular)`
- Scorer validation and golden gate: not run because identity failed

### Improvement/regression

The 1.5B model correctly recognized obvious appended metadata but also removed
legitimate specialization qualifiers and normalized Unicode punctuation. On
canonical titles it similarly over-trimmed role specializations. This violates
the conservative representation prerequisite and risks semantic drift.

### Conclusion and proposed next step

Reject without scoring. Make one bounded capacity check using the exact same
frozen prompt and qwen2.5:7b. Require 27/27 disjoint identity before any scoring
call; do not tune the prompt from canonical outputs.

## Attempt 82: qwen7 conservative scraped-title normalization

Status: rejected against preregistered byte-identity criterion; no scoring run

### Strategy and rationale

Repeat Attempt 81's preference-blind title extraction with qwen2.5:7b, keeping
the prompt byte-identical. A larger instruction model may distinguish appended
feed metadata from legitimate parenthetical and hyphenated specialization while
preserving punctuation. This is the only changed variable.

### Exact configuration

- Title normalizer: `qwen2.5:7b`, temperature 0, Ollama JSON mode
- System/user prompts, unique-title identity set, remaining prospective scorer
  configuration, and prohibitions: exactly Attempt 81
- Promotion prerequisite: 27/27 byte-identical fingerprint-disjoint titles;
  then full 52-row scorer validation must preserve or improve Attempt 61 before
  golden
- Probe command: `TITLE_NORMALIZER_MODEL=qwen2.5:7b` with
  `/tmp/probe_title_normalizer.py`

### Evaluation results

- Unique fingerprint-disjoint titles: 27
- Byte-identical disjoint outputs: 26/27
- Sole disjoint change:
  `Staff Engineer, AI Productivity a month ago New York, New York, United States · Fully Remote ·`
  -> `Staff Engineer, AI Productivity`
- Canonical changes (inspection only):
  - `Frontend Platform Engineer, JavaScript Infrastructure 2 days ago Fully Remote`
    -> `Frontend Platform Engineer, JavaScript Infrastructure`
  - same title with `5 days ago` -> same normalized title
- All other 22 unique canonical titles were byte-identical
- Scorer validation/golden: not run under this attempt because 27/27 identity
  was the preregistered prerequisite

### Improvement/regression

Qwen7 eliminated the semantic drift seen with 1.5B: it preserved every
genuinely clean held-out and canonical title and changed only three values with
obvious appended feed metadata. However, the attempt's literal 27/27 identity
criterion failed because the original premise that every held-out title was
clean was false.

### Conclusion and proposed next step

Close this attempt without scoring. Start a separate source-grounded attempt
with the frozen qwen7 prompt: accept the one held-out cleanup as intended input
normalization, score all 52 disjoint rows, and require aggregate metrics to
preserve or improve Attempt 61 before golden. Do not alter the prompt from
canonical outputs.

## Attempt 83: qwen7 title normalization, full disjoint scoring

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Apply Attempt 82's frozen qwen7 title normalizer uniformly, including the one
fingerprint-disjoint row whose raw title contains obvious appended age,
location, and remote-badge text. The identity probe established that every
other clean held-out title remains byte-identical. Now measure the actual cp200
scorer effect across all 52 rows before any golden run.

### Exact configuration

- Title normalizer model and exact system/user prompts: Attempt 82
- qwen2.5:1.5b location normalizer and exact prompt; remote -> `fully remote`;
  other location categories canonical: Attempt 61
- Disjoint scoring: committed canonical messages/passages, required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, temperature 0,
  canonical parser
- Acceptance: preserve or improve 33/52 exact, 0.3529 numeric MAE, and N/A
  behavior across all 52 rows
- Runtime retrieval if promoted: Attempt 61 expanded-query BGE top 10, Jina
  tiny top 2
- Validation command uses `NORMALIZE_TITLE=true` with
  `/tmp/eval_cp200_metadata_anchor.py`
- No prompt tuning, preference/category branch, score mapping, case-specific
  condition, or golden-derived parameter

### Evaluation results

- Invalid dry run (discarded before golden): 30/52 exact, 0.4118 MAE. Harness
  audit found it still rendered hybrid as Attempt 72's rejected
  `hybrid (partly onsite)` instead of this attempt's canonical `hybrid`.
- Corrected preregistered run: 32/52 exact (0.6154), numeric MAE 0.3725,
  predicted distribution 0=1, 1=3, 2=8, 3=10, 4=24, 5=6

### Improvement/regression

The exact configuration lost one exact case and added one numeric absolute-error
point relative to Attempt 61's disjoint baseline. Since the normalizer changed
only one held-out title, that cleanup directly caused the regression; all other
rows remained byte-identical at the title field.

### Conclusion and proposed next step

Reject title replacement before golden. Restore Attempt 61 metadata. Combine
the two best complementary evidence views—Jina semantic reranking and cp200
pointwise reranking—by having cp200 score each canonical view and selecting its
own more confident direct response. No score values are averaged or remapped.

## Attempt 84: cp200 confidence routing across evidence views

Status: failed

### Strategy and rationale

Generate Attempt 61's Jina top-two evidence view and Attempt 63's cp200
pointwise top-two evidence view. Make an independent canonical cp200 call on
each pair with first-token log probabilities enabled. Return the direct cp200
result from the view whose greedy first token has the higher model probability.
This lets the required scoring LLM select the evidence representation it finds
less ambiguous, without a helper router or score fusion.

The route is uniform and parameter-free. Confidence is used only to select one
complete source-evidence view; scores are not averaged, calibrated, rounded, or
mapped. Ties prefer the lower-cost Jina view.

### Exact configuration

- Scoring LLM for availability, pointwise passage ranking, both view scores,
  and returned result:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Location normalization/rendering, qwen2.5:1.5b query expansion and exact
  prompt, BGE-small atomic top-10 candidates, canonical messages, temperature,
  parsing, and availability fallback: Attempt 61
- View A: Jina tiny against raw preference, top two
- View B: first six expanded-query BGE candidates scored independently by
  greedy canonical cp200 integer; numeric descending, N/A below numeric,
  stable dense-rank ties; top two
- Each view call: `/api/chat`, canonical two-passage prompt, temperature 0,
  `logprobs=true`, `top_logprobs=20`
- Router: compare the log probability of each call's actual greedy first token;
  return View B result only when strictly greater, otherwise View A result
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-84 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true EVIDENCE_VIEW_ROUTING=confidence make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No title normalization, returned-score mapping/aggregation, preference/category
  branch, case-specific condition, or fitted/golden-derived parameter

### Evaluation results

- Golden exact accuracy: 25/53 = 0.4717
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8000
- Predicted distribution: 0=2, 1=11, 2=10, 3=10, 4=6, 5=11, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-84/`

### Improvement/regression

Confidence routing matched Attempt 61's exact count, preserved N/A, and reduced
MAE by 0.02, but remained 0.04 above the MAE gate and four exact cases short.
It improved on pointwise Attempt 63 by two exact cases but added 0.04 MAE.
First-token confidence did not reliably choose the exact-label winner when the
two evidence views disagreed.

### Conclusion and proposed next step

Reject the expensive two-view router. Isolate a narrower confidence use that can
be selected entirely on disjoint validation: present the exact same two
passages in forward and reverse order, then return cp200's more confident direct
response. No evidence is added, removed, or rewritten.

## Attempt 85: confidence selection across snippet order

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

The scorer is order-sensitive even when the two source passages are identical.
Make two canonical cp200 calls: selected passages in retriever order and the
same passages reversed. Return the direct result whose actual greedy first token
has higher model probability; ties keep retriever order. This is an
inference-stability technique over an invariant evidence set, not score fusion.

Unlike candidate-generation experiments, this can be selected on all 52
fingerprint-disjoint rows because their committed prompts contain exactly two
passages. Golden is run only if exact, MAE, and N/A behavior preserve or improve
the canonical validation baseline.

### Exact configuration

- Scoring LLM: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Both order calls: canonical system/user messages, temperature 0,
  `/api/chat`, `logprobs=true`, `top_logprobs=20`
- Router: compare log probability of each call's actual greedy first token;
  reverse only when strictly greater
- Disjoint prompt metadata: qwen2.5:1.5b location normalization with exact
  Attempt 60 prompt; remote -> `fully remote`; other values canonical
- Prospective runtime evidence: Attempt 61 expanded-query BGE top 10 and Jina
  top two; only final ordering is varied
- Validation script: `/tmp/eval_cp200_order_confidence.py`
- Planned gate command if validation accepts: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-85 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true FINAL_ORDER_ROUTING=confidence make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No score averaging/mapping, preference/category branch, case-specific
  condition, or fitted/golden-derived parameter

### Evaluation results

- Disjoint exact accuracy: 32/52 = 0.6154
- Numeric MAE: 20/51 = 0.3922
- Forward order selected: 24/52; reverse selected: 28/52
- Predicted distribution: 1=5, 2=7, 3=13, 4=21, 5=6
- Golden gate: not run

### Improvement/regression

Confidence order selection lost one exact case and added two numeric error points
relative to the canonical disjoint baseline. Although it chose each order about
half the time, token confidence did not correlate with better ordinal accuracy.

### Conclusion and proposed next step

Reject. Abandon confidence routing. Test reproducible low-temperature decoding
directly on the full disjoint set with a small preregistered temperature/seed
sweep; return only one direct cp200 sample per case, with no vote or score
aggregation.

## Attempt 86: fixed-seed low-temperature decoding sweep

Status: failed

### Strategy and rationale

The cp200 response distribution is concentrated but not degenerate. A fixed
low-temperature sample can escape a systematically compressed greedy choice
while remaining reproducible through an explicit seed. Evaluate six
predeclared configurations on all 52 fingerprint-disjoint rows. Each case has
one direct cp200 response—there is no self-consistency vote, score averaging,
mapping, or post-processing.

Promote the configuration with lexicographically best full-set metrics: highest
exact accuracy, then lowest numeric MAE, then lower temperature, then lower
seed. It must also preserve N/A behavior relative to the greedy baseline.

### Exact configuration

- Scoring LLM: `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Candidate `(temperature, seed)` pairs, in evaluation order:
  `(0.1, 42)`, `(0.1, 1729)`, `(0.2, 42)`, `(0.2, 1729)`, `(0.4, 42)`,
  `(0.4, 1729)`
- Messages/parser: committed canonical validation system/user messages and
  canonical response parser
- Metadata: qwen2.5:1.5b exact Attempt 60 location normalizer; remote ->
  `fully remote`, other categories canonical
- Dataset: all 52 rows in
  `src/python/ai_scorer/training/data/export/val.jsonl`
- Prospective runtime retrieval: Attempt 61
- Validation script: `/tmp/eval_sampling_sweep.py`
- No multiple-response aggregation, preference/category branch, case-specific
  condition, score mapping, or golden-derived parameter

### Evaluation results

- `(0.1, 42)`: 31/52 exact, 0.3922 MAE; distribution
  0=1, 1=3, 2=8, 3=10, 4=23, 5=7
- `(0.1, 1729)`: **33/52 exact, 0.3529 MAE**; distribution
  0=1, 1=3, 2=7, 3=11, 4=24, 5=6
- `(0.2, 42)`: 30/52 exact, 0.4118 MAE
- `(0.2, 1729)`: 31/52 exact, 0.3922 MAE
- `(0.4, 42)`: 31/52 exact, 0.4118 MAE
- `(0.4, 1729)`: 31/52 exact, 0.3922 MAE
- Every configuration predicted the expected N/A row numerically, matching the
  established greedy validation N/A behavior
- Selected by preregistered ordering: `(0.1, 1729)`
- Golden exact accuracy: 25/53 = 0.4717
- Golden N/A precision/recall/F1: 1.0/1.0/1.0
- Golden mean absolute error: 0.8400
- Golden result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-86/`

### Improvement/regression

The selected configuration is byte-for-byte equal to greedy aggregate metrics
and distribution on the full disjoint set. The other five configurations lost
two or three exact cases and added two or three numeric error points, so no
higher-temperature setting is promoted.

On golden, the selected sample tied Attempt 61's exact count, preserved N/A,
and added 0.02 MAE. Held-out metric parity did not translate into a gate gain.

### Conclusion and proposed next step

Reject sampling and restore deterministic temperature 0. Test the one locally
supported cross-encoder family member not yet isolated as a reranker:
`jina-reranker-v2-base-multilingual`. Require it to beat Jina tiny on a
fingerprint-disjoint passage-ranking proxy before golden.

## Attempt 87: Jina v2 base cross-encoder reranking

Status: rejected on disjoint retrieval proxy; golden gate not run

### Strategy and rationale

Replace Jina tiny with the substantially larger
`jina-reranker-v2-base-multilingual` cross-encoder while keeping candidate
generation and final scoring identical to Attempt 61. The v2 model has higher
capacity and a newer sliding-window cross-encoder architecture; it may better
separate central technical duties from semantically related boilerplate.

Before golden, compare it to Jina tiny on the 30 fingerprint-disjoint
validation queries whose job fingerprint appears more than once. For each such
query, pool the unique committed passages from all preferences for the same job
and treat that row's two committed passages as relevant. Promote v2 only if it
strictly improves recall@2, using MRR as a secondary metric. This proxy uses no
golden data or expected score label.

### Exact configuration

- Candidate reranker: `jinaai/jina-reranker-v2-base-multilingual` through
  FastEmbed 0.8.0 `TextCrossEncoder`
- Retrieval proxy baseline: `jinaai/jina-reranker-v1-tiny-en`
- Proxy query: byte-exact preference guidance; candidate pool: exact committed
  source passages grouped by disjoint job fingerprint; metrics recall@2 and MRR
- Proxy script: `/tmp/benchmark_rerankers.py`
- Prospective scorer configuration: Attempt 61 except candidate reranker model;
  BGE-small expanded-query top 10, v2 cross-encoder top 2, required cp200 final
  scorer, temperature 0
- Planned gate command if proxy accepts: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-87 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true RERANKING_MODEL=jinaai/jina-reranker-v2-base-multilingual make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt change, score aggregation/mapping, preference/category branch,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Jina tiny baseline: 30 queries, recall@2 0.4667, MRR 0.7772
- Jina v2 base: 30 queries, recall@2 0.4667, MRR 0.6898
- Golden gate: not run because v2 did not strictly improve recall and regressed
  the secondary metric

### Improvement/regression

The 1.1 GB v2 model tied the 0.13 GB tiny model on held-out top-two passage
recall and reduced mean reciprocal rank by 0.0874. Greater model capacity did
not produce a stronger ranking signal for these job-preference passages.

### Conclusion and proposed next step

Reject before golden and retain Jina tiny. Isolate raw-preference candidate
generation followed by Jina top-two reranking. Prior raw experiments either
ended at dense top two or reserved a raw result in the final budget; this tests
raw BGE only as a top-10 recall filter while preserving Jina's full choice.

## Attempt 88: raw-preference BGE candidates plus Jina reranking

Status: failed

### Strategy and rationale

Retrieve the top ten atomic passages with BGE-small against the byte-exact
preference, then rerank all ten with Jina tiny against that same preference and
select its top two. This removes LLM query-expansion drift while retaining the
successful cross-encoder stage. It differs from the raw BGE baseline (which
sent dense top two directly) and Attempt 59 (which forced a raw dense result
into one final slot).

The disjoint export lacks source descriptions, so it cannot regenerate a raw
top-10 pool. The final cp200 prompt, metadata, message contract, and two-passage
budget remain the validated Attempt 61 configuration; only source ranking
changes.

### Exact configuration

- Availability: Attempt 61 raw-preference BGE-small top two and required cp200
  canonical call; early return on N/A
- Location normalization/rendering: Attempt 61
- Candidate generation: `BAAI/bge-small-en-v1.5`, byte-exact raw preference,
  atomic chunks, top 10; query expansion output is not used
- Reranking: `jinaai/jina-reranker-v1-tiny-en`, raw preference, top two
- Final scoring: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical prompt,
  temperature 0; direct response only
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-88 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true CANDIDATE_RETRIEVAL_MODE=raw_only make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt change, score aggregation/mapping, preference/category branch,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Golden exact accuracy: 20/53 = 0.3774
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9200
- Predicted distribution: 0=5, 1=10, 2=9, 3=11, 4=6, 5=9, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-88/`

### Improvement/regression

Raw candidate generation lost five exact cases and added 0.10 MAE relative to
Attempt 61. Jina could not recover the passages found by semantic expansion,
even with ten raw dense candidates. Literal preference matching alone has
insufficient recall for duties and synonyms.

### Conclusion and proposed next step

Reject and restore semantic expansion. Test a single combined BGE query
containing the byte-exact preference followed by its expansion. Unlike RRF, it
does not allocate or fuse candidate ranks; unlike raw-only, it retains semantic
synonyms. Jina still has unrestricted choice among the resulting top ten.

## Attempt 89: concatenated raw-plus-expanded dense query

Status: failed

### Strategy and rationale

Embed one query consisting of the raw preference, a newline, and the exact
qwen2.5:1.5b semantic expansion. Retrieve BGE-small top ten and rerank them with
Jina tiny against the raw preference. Concatenation preserves literal
preference terms and expanded role/duty synonyms in one candidate-generation
vector without the agreement bias and candidate truncation introduced by RRF.

The disjoint export cannot rerun source retrieval; final message shape,
metadata, two-passage budget, and scorer behavior remain the validated Attempt
61 setup.

### Exact configuration

- Availability, location normalization/rendering, final cp200 prompt,
  temperature 0, parsing, and fallbacks: Attempt 61
- Expansion: qwen2.5:1.5b, exact Attempt 8 prompt, temperature 0, JSON
- Candidate query: `{raw_preference}\n{expanded_search_query}`
- Candidate retrieval: `BAAI/bge-small-en-v1.5`, atomic chunks, top 10
- Reranking: `jinaai/jina-reranker-v1-tiny-en` against raw preference, top 2
- Final scorer: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, direct response only
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-89 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true CANDIDATE_RETRIEVAL_MODE=raw_plus_expanded_query make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt change, score aggregation/mapping, preference/category branch,
  case-specific condition, or golden-derived parameter

### Evaluation results

- Golden exact accuracy: 22/53 = 0.4151
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8800
- Predicted distribution: 0=5, 1=10, 2=8, 3=13, 4=5, 5=9, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-89/`

### Improvement/regression

Concatenation lost three exact cases and added 0.06 MAE relative to Attempt 61.
The raw guidance shifted the dense vector enough to displace useful
expanded-query candidates; it did not provide the complementarity sought.

### Conclusion and proposed next step

Reject and restore expansion-only candidate generation. Make one bounded
capacity comparison for the same frozen query-expansion prompt using
qwen2.5:7b; no other retrieval or scoring variable changes.

## Attempt 90: qwen2.5 7B semantic query expansion

Status: failed

### Strategy and rationale

Replace qwen2.5:1.5b with qwen2.5:7b for the exact Attempt 8 semantic query
expansion task. A larger instruction model may preserve qualifiers and produce
more precise role-duty synonyms than the 1.5B model while avoiding the broader
drift observed from Attempt 43's different 3B expansion setup. Expansion is
cached by exact guidance and never sees a job or target score.

### Exact configuration

- Query expansion model: `qwen2.5:7b`, temperature 0, Ollama JSON mode
- Exact expansion system/user prompt and output field: Attempt 8
- Availability, location normalization/rendering, BGE-small expanded-query top
  10, Jina tiny against raw preference top 2, canonical final scorer prompt,
  temperature, parsing, and fallback: Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-90 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true QUERY_EXPANSION_MODEL=qwen2.5:7b make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt change to scorer, score aggregation/mapping, preference/category
  branch, case-specific condition, or golden-derived parameter

### Evaluation results

- Golden exact accuracy: 21/53 = 0.3962
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9000
- Predicted distribution: 0=4, 1=11, 2=8, 3=13, 4=5, 5=9, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-90/`

### Improvement/regression

The larger expansion model lost four exact cases and added 0.08 MAE relative
to Attempt 61. It also remained five exact cases below the separate 3B
expansion attempt. Extra expansion capacity did not improve retrieval; the
larger model's reformulations appear to have displaced useful, compact duty
terms produced by qwen2.5:1.5b.

### Conclusion and proposed next step

Reject and restore qwen2.5:1.5b. Keep Jina's top-two membership decision, but
present those two snippets in their original BGE relevance order. This cleanly
separates evidence-set selection from evidence presentation and better matches
the dense-ranked ordering used by the scorer's training pipeline.

## Attempt 91: preserve dense order after cross-encoder selection

Status: failed

### Strategy and rationale

Use the exact Attempt 61 retrieval and reranking models. Jina still selects the
two-member evidence set from BGE's expanded-query top ten, but the selected
snippets are restored to their original BGE relevance order before final
scoring. Cross-encoder scores are well suited to set membership, while their
fine-grained ordering can be unstable; preserving the first-stage relevance
order also matches the dense-ranked evidence presentation used by the scorer's
training/export pipeline.

The change is query- and case-independent. It does not inspect scores or
labels, and does not alter which snippets are selected.

### Exact configuration

- Availability, qwen2.5:1.5b expansion prompt, BGE-small expanded-query top 10,
  Jina tiny raw-preference top-two membership, location normalization/render,
  final cp200 prompt, parsing, and fallback: Attempt 61
- Evidence presentation: selected Jina pair restored to original BGE candidate
  order; no change to set membership
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-91 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true PRESERVE_CANDIDATE_ORDER=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No prompt, model, score aggregation/mapping, preference/category branch,
  deterministic scoring rule, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Golden exact accuracy: 25/53 = 0.4717
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8400
- Predicted distribution: 0=4, 1=9, 2=11, 3=12, 4=5, 5=9, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-91/`

### Improvement/regression

Exact accuracy tied Attempt 61, but MAE regressed by 0.02. Preserving the dense
order neither recovered any additional exact cases nor improved the magnitude
of errors, so cross-encoder presentation order is not the dominant bottleneck.

### Conclusion and proposed next step

Reject and restore Jina score order. Audit the disjoint validation export and
golden run by score/category and message structure to identify a general,
reproducible failure mode before changing another component.

## Attempt 92: responsibility-focused semantic query expansion

Status: rejected during query-contract audit; golden gate not run

### Strategy and rationale

An evidence audit found a general candidate-generation mismatch. For a
central-work criterion, the original expansion frequently produced candidate
qualification phrases (for example, experience and proficiency) rather than
language describing day-to-day duties or how central the work is. Jina then
selected generic skill lists and employer prose, leaving cp200 without direct
evidence of role centrality. This also applies to other criteria whose strength,
scope, or limitations matter.

Keep qwen2.5:1.5b, BGE, Jina, the two-snippet contract, and final scorer frozen.
Change only the expansion instruction so its query targets job-posting evidence
about responsibilities, scope, conditions, and explicit limitations while
preserving the criterion's intensity. First audit outputs for the three unique
gate preferences and representative disjoint preferences; then run the full
gate if they are faithful. The expansion model sees no job, score, label, case
identifier, or golden rationale.

### Exact configuration

- Query model: `qwen2.5:1.5b`, Ollama JSON mode, temperature 0
- Query-expansion system prompt:

  ```text
  Transform one candidate job preference into one compact semantic search query made of language likely to appear in a job posting as direct evidence for or against that criterion. Preserve its exact strength, exclusivity, and qualifiers. Prioritize core responsibilities, day-to-day work, role scope, work conditions, and explicit opposite or limiting language. Avoid generic company claims and generic candidate qualifications or skill lists unless the preference itself is about a qualification. Do not evaluate a job, invent job facts, or assign a score. Return one JSON object with exactly one string field named search_query.
  ```
- Query-expansion user prompt: exact `Preference Guidance: {guidance}` contract
- Availability, location normalization/rendering, BGE-small query top 10, Jina
  tiny raw-guidance top 2 in Jina score order, canonical final cp200 prompt,
  parsing, and fallback: Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-92 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true QUERY_EXPANSION_PROFILE=responsibility_evidence make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated job evidence, score aggregation/mapping, preference/category
  branch, deterministic scoring rule, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Three unique gate-guidance expansions audited before scoring
- Ten unique fingerprint-disjoint validation-guidance expansions reserved for
  the follow-up model-capacity check
- For `It requires a lot of coding`, qwen2.5:1.5b returned
  `requires extensive programming experience, proficient in scripting
  languages, knowledge of software development methodologies`
- Retrieval for the audited coding case selected the same two generic snippets
  as Attempt 61
- Golden gate: not run because the required query distinction was not achieved

### Improvement/regression

The 1.5B model ignored the instruction to target responsibilities rather than
generic qualifications. Its output remained qualification-heavy, so the
candidate set and Jina pair did not change in the representative failure. This
would not test the stated strategy.

### Conclusion and proposed next step

Reject without spending a golden run. Keep the frozen prompt and test the local
qwen2.5:7b helper strictly for instruction-following. Promote it only if the
three gate preferences and all ten unique disjoint-validation preferences
produce faithful, compact queries.

## Attempt 93: 7B responsibility-focused semantic query expansion

Status: failed

### Strategy and rationale

Use qwen2.5:7b for Attempt 92's frozen responsibility-focused expansion prompt.
The 7B helper passed the preregistered instruction-following audit: it expressed
the coding criterion as `requires extensive coding responsibilities`, preserved
the backend/frontend contrast and fully-remote intensity, and produced faithful
compact queries for all ten unique fingerprint-disjoint validation preferences.
It sees no job or evaluation label. All retrieval, reranking, scorer, and prompt
components remain frozen at Attempt 61.

This differs from failed Attempt 90 in both objective and output: Attempt 90
used the original broad title/duty/technology prompt, while this query is
explicitly constrained to direct responsibility, scope, condition, and
limitation evidence.

### Exact configuration

- Query model: `qwen2.5:7b`, JSON mode, temperature 0
- Query expansion system/user prompt: exact Attempt 92 prompt
- Audited gate queries:
  - fully remote: `fully remote role`
  - backend/platform contrast: `backend engineer OR infrastructure engineer OR platform engineer NOT frontend engineer`
  - coding centrality: `requires extensive coding responsibilities`
- Audited fingerprint-disjoint queries: exact outputs retained in the run
  transcript; all ten preserved their source criterion and qualifiers
- Availability, BGE-small top 10, Jina tiny raw-guidance top 2 in Jina order,
  location normalization/rendering, canonical cp200 scorer prompt, temperature
  0, parser, and fallbacks: Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-93 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true QUERY_EXPANSION_PROFILE=responsibility_evidence QUERY_EXPANSION_MODEL=qwen2.5:7b make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated job evidence, score aggregation/mapping, preference/category
  branch, deterministic scoring rule, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Golden exact accuracy: 20/53 = 0.3774
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.9400
- Predicted distribution: 0=5, 1=11, 2=8, 3=11, 4=6, 5=9, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-93/`

### Improvement/regression

The responsibility-focused 7B query lost five exact cases and added 0.12 MAE
relative to Attempt 61. It improved neither the coding group nor overall
retrieval despite satisfying the query contract, and was one exact case worse
than the same 7B model with the original broad prompt in Attempt 90. A compact,
faithful query is insufficient when both evidence slots are still optimized for
topical relevance and can omit role-scope context.

### Conclusion and proposed next step

Reject and restore the qwen2.5:1.5b Attempt 61 query. Audit a coverage-aware,
two-channel evidence set: one criterion-relevant passage plus one
preference-blind passage about central responsibilities/day-to-day scope. Keep
both passages verbatim and use the same BGE/Jina models within each channel.

## Attempt 94: domain-trained extractive selector with native-format parser

Status: failed

### Strategy and rationale

Revisit Attempt 76 after identifying that its sibling chat-full checkpoint uses
a stable native tagged-array response, `[selected_indices]\n[i,j]`, rather than
the requested JSON object. Parse either the requested JSON object/list or that
strict array form, then validate exactly two distinct zero-based in-range
integers. The selector can only return original source fragments. If its output
is empty, malformed, duplicated, or out of range, fall back to Attempt 61's
Jina selection for the same candidate pool.

The domain-trained selector audit produced valid selections for four of six
representative central-work cases. It replaced generic employer/qualification
text with concrete `design and build`, `integrating`, and `building ... APIs
and UIs` duties in three cases. Two empty outputs were detected and would take
the Jina fallback; no generated text can enter the scorer prompt.

### Exact configuration

- Availability, location normalization/rendering, qwen2.5:1.5b Attempt 8 query
  expansion, BGE-small expanded-query top 10, final cp200 canonical prompt,
  parsing, and scoring temperature: Attempt 61
- Selector model:
  `ai-scorer-qwen25:fp-v2-balanced-chat-full-cp200-f16`, temperature 0,
  Ollama JSON mode
- Selector system prompt:

  ```text
  Choose exactly 2 job-evidence fragments most diagnostic of whether the candidate preference is satisfied. Prefer explicit central support or contradiction over vague, generic, or administrative text. Indices are zero-based. Do not score the job, rewrite evidence, or invent text. Return JSON only with one field: selected_indices.
  ```
- Selector user fields: exact preference, normalized title and location, and
  the ten indexed BGE source fragments
- Accepted contracts: JSON object `{"selected_indices":[i,j]}`, bare JSON
  `[i,j]`, or checkpoint-native tagged text containing a final `[i,j]`; every
  path undergoes the same strict arity/type/distinctness/range validation
- Invalid selector fallback: Jina tiny against raw preference, top two
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-94 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true EVIDENCE_SELECTION_MODE=compact_llm EVIDENCE_SELECTOR_MODEL=ai-scorer-qwen25:fp-v2-balanced-chat-full-cp200-f16 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, score aggregation/mapping, preference/category
  branch, deterministic scoring rule, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Golden exact accuracy: 24/53 = 0.4528
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8200
- Predicted distribution: 0=5, 1=8, 2=10, 3=9, 4=10, 5=8, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-94/`

### Improvement/regression

The domain selector lost one exact case while tying Attempt 61's MAE. It raised
several underpredictions by selecting stronger duties, but also displaced
correct Jina evidence in other cases. The oracle union of Attempts 61 and 94 is
only 28/53, one short of the gate, so selector-output routing alone cannot pass.

### Conclusion and proposed next step

Reject and restore Jina. Focused retrieval and complete-description scoring
have a much larger 33/53 oracle union. Test whether cp200's own greedy
first-token confidence can select between those two cp200 answers without a
second model, score averaging, or remapping.

## Attempt 95: cp200 confidence routing between focused and global evidence

Status: failed

### Strategy and rationale

Run the required cp200 scorer on two source-grounded views: Attempt 61's two
Jina passages and the complete normalized job description as one snippet. Ask
Ollama for first-token log probabilities on each otherwise-canonical call and
return the result with higher greedy-token confidence. This is a standard
model-confidence routing strategy for complementary RAG contexts. Both
candidate answers come from the required scoring LLM; no score is averaged,
mapped, altered, or supplied by a helper.

The earlier semantic routers (Attempts 42 and 48) asked another LLM to predict
view utility. This attempt instead measures cp200's certainty in its own direct
answer while preserving the exact system/user scoring contract.

### Exact configuration

- Availability, qwen2.5:1.5b expansion, BGE-small top 10, Jina tiny raw-guidance
  top 2, normalized/rendered location, scorer prompt, parser, and fallback:
  Attempt 61
- Focused view: exact Attempt 61 Jina pair
- Global view: one snippet containing the complete normalized job description
- Both view calls: required
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`, canonical messages,
  temperature 0, Ollama `logprobs=true`, `top_logprobs=20`
- Router: select the complete cp200 result whose generated first token has the
  greater log probability; stable focused-view tie break
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-95 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true EVIDENCE_VIEW_ROUTING=confidence_global make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated evidence, score aggregation/mapping, preference/category
  branch, case-specific condition, or golden-derived parameter

### Evaluation results

- Golden exact accuracy: 23/53 = 0.4340
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.8600
- Predicted distribution: 0=1, 1=12, 2=10, 3=13, 4=4, 5=10, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-95/`

### Improvement/regression

Confidence routing lost two exact cases and added 0.04 MAE relative to Attempt
61. The first-token probability measures decisiveness under each context, not
whether that context supports the more accurate assessment; confident global
errors displaced correct focused answers. It recovered only a small fraction
of the 33-case oracle union.

### Conclusion and proposed next step

Reject and restore direct focused scoring. Address a different distribution
shift: training and fingerprint-disjoint validation guidance is complete and
first-person, while all six unique runtime strings are impersonal or
third-person fragments. Canonicalize only that linguistic perspective and keep
every complete first-person guidance byte-identical.

## Attempt 96: first-person form canonicalization for preference fragments

Status: rejected during normalization audit; golden gate not run

### Strategy and rationale

Canonicalize only impersonal or third-person preference fragments into one
natural first-person statement before the final cp200 call. The scorer's entire
training export and all ten unique fingerprint-disjoint validation criteria
start with `I` or `My`; those strings are returned byte-for-byte without an LLM
call. The six unique runtime criteria use forms such as `Prefers ...` or `It
requires ...`, which are outside that learned message distribution.

The qwen2.5:7b normalizer sees only the preference, never a job, evidence,
score, label, case identifier, or rationale. Original guidance remains in the
availability call, query expansion, and Jina reranking, so this attempt changes
only the final scorer's linguistic presentation after N/A has already been
decided.

### Exact configuration

- First-person invariant: guidance beginning with `I` or `My` (case-insensitive,
  ignoring leading whitespace) is returned byte-for-byte; all 52 disjoint
  validation rows therefore retain their exact canonical messages
- Normalizer: `qwen2.5:7b`, JSON mode, temperature 0, cached by exact guidance
- Normalizer system prompt:

  ```text
  Rewrite one impersonal or third-person candidate preference as one natural first-person preference statement. Preserve exactly the stated criterion, intensity, acceptable alternatives, exclusions, and qualifiers. Resolve only grammatical perspective or an impersonal pronoun; do not add role titles, duties, technologies, synonyms, examples, exceptions, opposite conditions, or new semantic detail. Do not evaluate a job or assign a score. Return one JSON object with exactly one string field named normalized_guidance.
  ```
- User prompt: `Preference Guidance: {exact_input}`
- Availability and retrieval/reranking guidance: exact original bytes
- Final scoring guidance: exact normalized string only for non-first-person
  inputs
- Location normalization/rendering, qwen2.5:1.5b expansion, BGE-small top 10,
  Jina tiny raw-guidance top 2, final system/user structure, parser, and fallback:
  Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-96 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated job evidence, score aggregation/mapping, preference/category
  branch, deterministic scoring rule, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Unique fingerprint-disjoint guidance identity: 9/10
- Runtime outputs beginning with `I`: 0/6
- Representative failures:
  - `It requires a lot of coding` remained byte-identical
  - `Prefers fully remote roles` remained byte-identical
  - `Prefers backend engineering work` became `Prefers to work on backend engineering`
- Golden gate: not run because the normalizer did not satisfy its form contract

### Improvement/regression

One disjoint criterion (`Developer tooling is a high priority for me...`) did
not begin with `I` or `My`, so the initial invariant was too narrow. More
importantly, qwen2.5:7b ignored the first-person instruction for every runtime
fragment and merely preserved or paraphrased third-person form. This would not
test the stated distribution-shift hypothesis.

### Conclusion and proposed next step

Reject without scoring. Extend the byte-identity invariant to complete personal
statements containing `me`, and strengthen the same form-only instruction with
an explicit `I` output contract and one unrelated grammatical example. Re-audit
all strings before any golden run.

## Attempt 97: constrained first-person canonicalization with one grammar example

Status: rejected during normalization audit; scorer validation and golden not run

### Strategy and rationale

Keep Attempt 96's final-call-only form normalization, but make the prerequisite
contract mechanically testable: every rewritten output must begin with `I`.
Preserve input byte-for-byte when it begins with `I`/`My` or contains the
personal pronoun `me`, covering all ten unique fingerprint-disjoint criteria.
Add one domain-unrelated grammar example to demonstrate perspective conversion
without teaching any evaluation category or score.

### Exact configuration

- Identity invariant: begins with `I` or `My`, or contains standalone `me`,
  case-insensitive; all ten unique disjoint criteria and all 52 rows remain
  byte-identical
- Normalizer model: `qwen2.5:7b`, JSON mode, temperature 0
- System prompt: Attempt 96 plus the explicit sentence: `The output MUST begin
  with the word 'I'; never leave it in 'Prefers', 'It requires', or another
  impersonal form.`
- Single grammar demonstration:
  - user: `Preference Guidance: Prefers quiet work environments`
  - assistant: `{"normalized_guidance":"I prefer quiet work environments"}`
- Target user prompt and all remaining scorer/retrieval configuration: Attempt
  96 / Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-97 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job-conditioned rewriting, generated job evidence, score aggregation or
  mapping, preference/category branch, case-specific condition, or
  golden-derived parameter

### Evaluation results

- Runtime outputs satisfying the `I` prefix: 6/6
- Unique disjoint guidance identity: 8/10
- Semantic contract failure: `It requires a lot of coding` became `I require a
  lot of coding`, changing the referent instead of expressing a preference for
  roles where coding is required
- Golden gate: not run

### Improvement/regression

The explicit output constraint fixed grammatical perspective for the other five
runtime strings, but the impersonal-pronoun case changed meaning. Two complete
disjoint statements without personal pronouns were also paraphrased. Since the
central coding criterion was not faithfully normalized, scorer results would
not isolate the stated strategy.

### Conclusion and proposed next step

Reject without scoring. Add one domain-unrelated referent-resolution example
for `It requires ...`, then score all 52 disjoint validation rows before golden
rather than requiring byte identity for the two complete impersonal statements.

## Attempt 98: referent-aware first-person canonicalization

Status: rejected on disjoint validation; golden gate not run

### Strategy and rationale

Retain Attempt 97's constrained form normalization and add one unrelated
example showing that impersonal `It requires X` describes a desired role, not a
candidate requirement. The identity invariant still preserves 8/10 unique
disjoint criteria byte-for-byte; the two complete impersonal validation
criteria may be paraphrased, so selection now requires the full 52-row scorer
validation to preserve or improve the canonical 33/52 exact, 0.3529 numeric MAE
baseline before any golden run.

### Exact configuration

- Model, temperature, JSON contract, identity invariant, system prompt, quiet-
  environment example, final-call-only application, and all retrieval/scorer
  settings: Attempt 97
- Additional grammar example:
  - user: `Preference Guidance: It requires frequent travel`
  - assistant: `{"normalized_guidance":"I prefer roles that require frequent travel"}`
- Validation: all 52 rows from
  `src/python/ai_scorer/training/data/export/val.jsonl`, exact canonical messages
  except the target guidance value after the same normalization function
- Acceptance before golden: exact >= 33/52, numeric MAE <= 0.3529, and preserve
  baseline N/A behavior
- Planned gate command if accepted: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-98 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job-conditioned rewriting, generated evidence, score aggregation/mapping,
  preference/category branch, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Runtime normalization audit: 6/6 faithful first-person statements; notably,
  `It requires a lot of coding` became `I prefer positions that require a lot
  of coding`
- Unique disjoint guidance identity: 8/10
- Disjoint validation exact accuracy: 29/52 = 0.5577
- Disjoint validation numeric MAE: 0.4314
- Disjoint validation N/A F1: 0.0
- Predicted distribution: 1=4, 2=7, 3=12, 4=25, 5=4, N/A=0
- Golden gate: not run

### Improvement/regression

Referent resolution fixed the important runtime semantic error, but the two
rewritten complete impersonal validation criteria caused the scorer aggregate
to lose four exact cases, add four numeric error points, and miss the expected
N/A. The response-only checkpoint remains sensitive even to faithful
paraphrases.

### Conclusion and proposed next step

Reject before golden. Insert a few-shot form classifier before rewriting: it
must distinguish missing-experiencer/referent fragments from complete
impersonal preference statements. Preserve exact source bytes on every false
decision, then audit all 16 unique runtime/disjoint strings.

## Attempt 99: few-shot fragment classifier before first-person normalization

Status: failed golden gate

### Strategy and rationale

Use qwen2.5:7b to classify grammatical completeness before the faithful Attempt
98 rewriter. The classifier returns only a boolean. Exact source bytes are kept
when false; true fragments alone are converted to first person. Four unrelated
grammar demonstrations cover incomplete `Prefers X` and `It requires Y` forms,
and complete `X is a priority` and `X is a key interest` forms. This directly
targets Attempt 71's all-false classifier failure without teaching a job domain,
preference category, case, or score.

### Exact configuration

- Personal-statement fast path: exact Attempt 98 (`I`/`My` prefix or standalone
  `me`) returns source bytes without classification
- Classifier: `qwen2.5:7b`, JSON mode, temperature 0; output field
  `needs_rewrite` must be a JSON boolean
- Classifier system prompt:

  ```text
  Classify only the grammatical form of one candidate-preference string. Set needs_rewrite=true for a fragment with a missing experiencer or an unresolved impersonal referent, such as 'Prefers X', 'Strong preference for X', or 'It requires Y'. Set needs_rewrite=false for a complete declarative preference, including 'X is a priority', 'X is a key interest', or a first-person statement. Do not interpret the criterion, paraphrase it, evaluate a job, or assign a score. Return one JSON object with exactly one boolean field named needs_rewrite.
  ```
- Few-shot forms/labels:
  - `Prefers quiet work environments` -> true
  - `It requires frequent travel` -> true
  - `Flexible hours are a priority, provided deadlines remain clear.` -> false
  - `Research is a key interest.` -> false
- True path: exact Attempt 98 first-person rewriter and two grammar examples
- False path: exact input bytes
- Acceptance audit: all ten disjoint strings unchanged; six runtime strings
  classified/rephrased faithfully; then direct validation must preserve 33/52,
  0.3529 MAE, and baseline N/A behavior before golden
- Remaining runtime, retrieval, final scorer, and planned gate command: Attempt
  98
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job input to classifier/rewriter, generated evidence, score mapping/fusion,
  preference/category branch, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Classifier audit: all 10 unique disjoint criteria preserved byte-for-byte
- Runtime audit: ambiguous `It requires...` and simple `Prefers...` fragments
  rewritten faithfully; complete multi-sentence criteria preserved
- Disjoint scorer behavior: byte-identical to the canonical 33/52, 0.3529 MAE
  baseline by exact message identity
- Golden exact accuracy: 25/53 = 0.4717
- Golden N/A precision/recall/F1: 1.0/1.0/1.0
- Golden mean absolute error: 0.7400
- Predicted distribution: 0=3, 1=11, 2=7, 3=12, 4=7, 5=10, N/A=3
- Result: **FAILED** exact accuracy; N/A and MAE passed
- Artifacts: `eval-results/experiments/cp200-attempt-99/`

### Improvement/regression

Exact accuracy tied Attempt 61, but total numeric error fell by four points,
bringing MAE from 0.82 to a passing 0.74. Coding error fell from 26 to 21 and
coding exact rose from 3 to 4, while one remote exact case regressed. The
validation-preserving form correction therefore improves ordinal magnitude but
does not recover the four additional exact cases needed.

### Conclusion and proposed next step

Keep the validated fragment classifier/normalizer as the new base because it
passes MAE and preserves disjoint messages. Combine it with Attempt 94's
source-constrained domain selector: the two changes address orthogonal failure
modes (linguistic form versus evidence selection), and malformed selector
output still falls back to Jina.

## Attempt 100: normalized scorer plus domain-trained extractive selection

Status: failed

### Strategy and rationale

Compose Attempt 99's validation-preserving, final-call-only preference form
normalization with Attempt 94's domain-trained extractive selector. The
normalizer reduces ordinal compression without changing retrieval. The
selector replaces generic topically related fragments with more concrete
verbatim duties when its strict source-index contract succeeds. These are
orthogonal stages; neither sees a label, expected score, case identifier, or
golden rationale.

### Exact configuration

- Fragment classifier, rewriter, audit invariants, and final normalized
  guidance: Attempt 99
- Availability, location normalization/rendering, qwen2.5:1.5b expansion,
  BGE-small expanded-query top 10, final system/user contract, parsing:
  Attempt 61
- Selector model, exact prompts, accepted native/JSON formats, strict source
  index validation, and invalid-output Jina fallback: Attempt 94
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-100 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true EVIDENCE_SELECTION_MODE=compact_llm EVIDENCE_SELECTOR_MODEL=ai-scorer-qwen25:fp-v2-balanced-chat-full-cp200-f16 make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No generated job evidence, score aggregation/mapping, preference/category
  branch, deterministic scoring rule, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Golden exact accuracy: 24/53 = 0.4528
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.7600
- Predicted distribution: 0=5, 1=10, 2=8, 3=7, 4=11, 5=9, N/A=3
- Result: **FAILED** exact accuracy; N/A and MAE passed at the threshold
- Artifacts: `eval-results/experiments/cp200-attempt-100/`

### Improvement/regression

The composition lost one exact case and added 0.02 MAE relative to normalized
Jina Attempt 99. Normalization did not make the selector's concrete passages
consistently useful to cp200; displaced Jina evidence still caused regressions.

### Conclusion and proposed next step

Reject and restore Jina. Narrow normalization to unresolved referents only:
concise `Prefers X` fields are semantically complete in a structured preference
field and did not improve backend scores, while their remote rewrite caused a
regression. For the one unresolved `It requires...` form, make the implicit role
referent and quantitative scope explicit without changing the criterion.

## Attempt 101: referent-only quantitative-scope normalization

Status: rejected during classifier audit; golden gate not run

### Strategy and rationale

Classify only missing impersonal referents. Preserve concise structured-field
forms such as `Prefers X` and `Strong preference for X`, all complete
declaratives, and all personal statements byte-for-byte. Rewrite only a form
such as `It requires Y`, because `It` has no antecedent in the standalone
scoring message. When quantitative scope is stated (`a lot`), make that scope
explicit as a large share of the role's work. This is semantic criterion
canonicalization, not score calibration.

The change preserves all ten fingerprint-disjoint validation criteria exactly,
and unlike Attempt 99 avoids the one remote regression introduced by rewriting
an already-unambiguous `Prefers fully remote roles` field.

### Exact configuration

- Referent classifier: `qwen2.5:7b`, JSON mode, temperature 0
- True examples: `It requires frequent travel`; false examples: `Prefers quiet
  work environments`, `Strong preference for quiet offices`
- Classifier output: exact boolean `needs_rewrite`; false path returns source
  bytes
- Rewriter: `qwen2.5:7b`, JSON mode, temperature 0
- Rewriter contract: make only the implicit desired-role referent and stated
  quantitative scope explicit; preserve criterion/qualifiers; output begins
  with `I`
- Rewriter examples:
  - `It requires frequent travel` -> `I prefer roles that require frequent travel`
  - `It requires a lot of public speaking` -> `I prefer roles where a large share of the work involves public speaking`
- Availability/retrieval/reranking guidance remains original; only the final
  cp200 call receives a rewritten criterion when the classifier is true
- All location, qwen2.5:1.5b expansion, BGE-small, Jina, final scorer, parser,
  and fallback settings: Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-101 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job input, generated evidence, score aggregation/mapping,
  preference/category branch, case-specific condition, or golden-derived
  parameter

### Evaluation results

- Target rewrite: `It requires a lot of coding` -> `I prefer roles where a
  significant amount of time is spent on coding`
- Runtime false positive: `Prefers roles that involve a lot of coding` was
  rewritten despite the classifier's explicit false example for `Prefers X`
- Disjoint false positive: `Roles involving incident response are a priority,
  ...` was rewritten
- Unique disjoint guidance identity: 9/10
- Golden gate: not run

### Improvement/regression

The target quantitative-scope rewrite was faithful, but the few-shot classifier
still exceeded its declared unresolved-referent scope. Proceeding would repeat
Attempt 98's validation regression and would not isolate referent resolution.

### Conclusion and proposed next step

Reject without scoring. Add a strict, general linguistic candidate guard before
the LLM: only standalone fields beginning with an impersonal pronoun or
demonstrative (`it`, `this`, or `that`) can enter referent classification. This
prevents false-positive rewriting of all subjectless `Prefers X` and complete
noun-subject forms while retaining LLM judgment inside the ambiguous class.

## Attempt 102: guarded unresolved-referent normalization

Status: failed

### Strategy and rationale

Bound Attempt 101's LLM classifier with a syntactic candidate guard. If the
trimmed guidance does not begin with standalone `it`, `this`, or `that`, return
the exact input bytes. Candidate strings still pass through the qwen2.5:7b
boolean classifier and faithful quantitative-scope rewriter. This is a standard
guardrail around an LLM classifier: code validates the input is within the
classifier's declared linguistic scope, while no preference category, job,
score, case identifier, or label is inspected.

The guard guarantees exact identity for all ten disjoint validation criteria
and five of six runtime criteria; only the genuinely unresolved `It requires a
lot of coding` form is eligible.

### Exact configuration

- Candidate guard regex: `^\s*(?:it|this|that)\b`, case-insensitive
- Non-candidate path: exact source bytes, no classifier/rewriter call
- Candidate classifier, examples, model, JSON contract, temperature: Attempt
  101
- Target rewriter and audited output: Attempt 101 (`I prefer roles where a
  significant amount of time is spent on coding`)
- Final-call-only application and all Attempt 61 location/retrieval/Jina/cp200
  settings unchanged
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-102 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job-conditioned normalization, generated evidence, score
  aggregation/mapping, preference/category branch, case-specific condition, or
  golden-derived parameter

### Evaluation results

- Invariant audit: 10/10 unique disjoint criteria byte-identical; only the one
  unresolved runtime form rewritten
- Target rewrite: `I prefer roles where a significant amount of time is spent
  on coding`
- Golden exact accuracy: 25/53 = 0.4717
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.7800
- Predicted distribution: 0=4, 1=10, 2=8, 3=13, 4=5, 5=10, N/A=3
- Result: **FAILED** exact accuracy and MAE
- Artifacts: `eval-results/experiments/cp200-attempt-102/`

### Improvement/regression

Guarding removed Attempt 99's remote regression, but the quantitative-scope
paraphrase lost two coding exact cases that Attempt 99's simpler first-person
form had recovered. Exact remained 25 and MAE regressed from 0.74 to 0.78.

### Conclusion and proposed next step

Reject the scope elaboration and restore the simpler referent rewrite. Route
evidence by the kind of source required: use preference-only LLM metadata to
identify criteria directly determined by location, apply cp200 pointwise
selection there, and apply the validated heading-only filter before Jina for
description-dependent criteria.

## Attempt 103: LLM evidence-scope routing with source-specific retrieval

Status: near-pass; failed exact threshold by one case

### Strategy and rationale

Classify each preference as `location_metadata` only when it is solely about
work arrangement/geography and an explicit location field can decide it;
otherwise classify it as `description`. The qwen2.5:7b classifier sees only the
preference. Location criteria use the domain-aligned cp200 pointwise cascade,
which previously reached 14/15 remote exact. Description criteria remove only
formatting-only headings before the Attempt 61 Jina reranker; that general
filter previously raised backend exact from 10 to 12 while retaining source
text.

Use the guarded, validation-identity-preserving first-person referent
normalizer, restored to its simpler form without Attempt 102's quantitative
elaboration.

### Exact configuration

- Evidence-scope classifier: `qwen2.5:7b`, JSON mode, temperature 0; exact
  labels `location_metadata|description`
- Classifier rule: location only for work arrangement/geography directly
  decidable from explicit location; role family, duties, technologies,
  intensity, culture, and quality require description
- Few-shot examples:
  - fully remote work -> location
  - Amsterdam or remote -> location
  - hands-on implementation -> description
  - backend over frontend -> description
- Location path: expanded-query BGE top 10, Jina top-4 shortlist, cp200
  canonical pointwise score per passage, keep top 2 in Jina order, then fresh
  required cp200 final call
- Description path: remove only Markdown/bold/short-colon heading-only atomic
  chunks, expanded-query BGE top 10, Jina raw-guidance top 2
- Normalizer guard/classifier: Attempt 102; rewriter restored to the simpler
  first-person role-referent prompt and travel example from Attempt 99
- Availability, normalized/rendered location, query expansion, final scorer
  prompt, parser, and fallbacks: Attempt 61
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-103 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true EVIDENCE_SCOPE_ROUTING=llm make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job input to routing/normalization, generated evidence, score
  aggregation/mapping, preference-key branch, case-specific condition, or
  golden-derived parameter

### Evaluation results

- Evidence-scope audit: 2/2 work-arrangement runtime criteria classified
  `location_metadata`; 4/4 remaining runtime and 10/10 disjoint criteria
  classified `description`
- Referent audit: all 10 disjoint criteria exact; only unresolved runtime form
  rewritten to `I prefer roles that require a lot of coding`
- Golden exact accuracy: 28/53 = 0.5283
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.6600
- Predicted distribution: 0=4, 1=9, 2=8, 3=11, 4=5, 5=13, N/A=3
- Result: **FAILED** exact threshold by 1/53; N/A and MAE passed
- Artifacts: `eval-results/experiments/cp200-attempt-103/`

### Improvement/regression

Relative to Attempt 99, source-specific routing gained three exact cases and
reduced MAE by 0.08. Remote improved to 14/15 exact with only one error point;
backend reached 11/19 with ten error points; coding retained 3/19 but improved
to 22 error points. The only location-criterion error is a hybrid posting scored
1 against a fully-remote-only preference.

### Conclusion and proposed next step

Keep the architecture. Clarify normalized `hybrid` as `hybrid (partly onsite)`
only when the audited evidence-scope classifier selects `location_metadata`.
Attempt 72 rejected this rendering when applied to all validation rows; scoped
routing leaves all 52 disjoint prompts byte-identical and expresses the work-
arrangement limitation only where location is the decisive evidence source.

## Attempt 104: explicit hybrid meaning on location-metadata path

Status: failed; behaviorally neutral

### Strategy and rationale

Preserve Attempt 103 exactly, but render normalized `hybrid` as `hybrid (partly
onsite)` only after the preference-only evidence-scope classifier has selected
`location_metadata`. Hybrid necessarily combines remote and onsite work; the
clarification makes the structured value self-explanatory for criteria that are
fully determined by work arrangement. It is not applied to description-routed
preferences, so every disjoint validation prompt remains exact.

### Exact configuration

- Evidence-scope classifier/audited labels, guarded referent normalization,
  location and description retrieval paths, all models/prompts/temperatures,
  final scorer, parser, and fallbacks: Attempt 103
- Location rendering:
  - `remote` -> `fully remote` (Attempt 61)
  - `hybrid` -> `hybrid (partly onsite)` only when evidence scope is
    `location_metadata`
  - `hybrid` remains canonical for every description-scoped criterion
  - `onsite` and `unknown` unchanged
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-104 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true EXPLICIT_HYBRID_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true EVIDENCE_SCOPE_ROUTING=llm make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job input to routing/normalization, generated evidence, score mapping or
  aggregation, preference-key branch, case-specific condition, or
  golden-derived parameter

### Evaluation results

- Golden exact accuracy: 28/53 = 0.5283
- N/A precision/recall/F1: 1.0/1.0/1.0
- Mean absolute error: 0.6600
- Predicted distribution: 0=4, 1=9, 2=8, 3=11, 4=5, 5=13, N/A=3
- Per-case outputs: byte-for-byte score-equivalent to Attempt 103
- Result: **FAILED** exact threshold by 1/53
- Artifacts: `eval-results/experiments/cp200-attempt-104/`

### Improvement/regression

The explicit hybrid phrase did not change cp200's answer for the hybrid
location criterion or any other case. It remained 1, so metrics and the full
per-case prediction vector were identical to Attempt 103.

### Conclusion and proposed next step

Reject the neutral rendering and restore canonical `hybrid`. Address the two
empty-description cases: when title is the only semantic evidence, normalize
obvious appended feed metadata before both availability and final scoring while
leaving clean titles and every non-empty-description row unchanged.

## Attempt 105: title cleanup only for empty descriptions

Status: pending targeted audit and golden evaluation

### Strategy and rationale

Apply the validated qwen2.5:7b title normalizer only when the normalized job
description is empty. Empty-description scoring depends entirely on structured
metadata, so appended posting age, location, and remote-badge text can obscure
the actual role title. Clean titles must remain byte-identical; all postings
with source description evidence bypass title normalization entirely.

This is narrower than Attempt 83's global title normalization, whose one
changed disjoint row had description evidence and regressed. The new guard
leaves that row and the complete 52-row disjoint export unchanged.

### Exact configuration

- Empty-description guard: exact
  `normalize_description_markdown(description)` is empty
- Title normalizer model/system/user prompt, JSON contract, temperature 0:
  Attempt 82/83
- Normalizer removes only appended feed metadata (posting age,
  work-arrangement badge, geography, salary, application status); preserves
  title wording/seniority/specialty/punctuation/case otherwise
- All evidence-scope routing, guarded referent normalization, location and
  description paths, models, prompts, scorer, and fallback: Attempt 103
- Hybrid rendering: restored to canonical `hybrid`
- Required scoring LLM:
  `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- Planned gate command: `PATH=/home/fabrizio/personal/cover_letter/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin FASTEMBED_CACHE_PATH=/tmp/fastembed_cache EVAL_OUTPUT_DIR=eval-results/experiments/cp200-attempt-105 NORMALIZE_JOB_LOCATION=true EXPLICIT_REMOTE_LOCATION=true NORMALIZE_PREFERENCE_GUIDANCE=true EVIDENCE_SCOPE_ROUTING=llm NORMALIZE_EMPTY_DESCRIPTION_TITLE=true make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- No job input to preference routing/normalization, generated evidence, score
  mapping/aggregation, preference-key branch, case-specific condition, or
  golden-derived parameter

### Evaluation results

Pending.

## Attempt 107: consolidated Attempt 103 rerun

Removed later empty-description title normalization and explicit hybrid-location
rendering so the implementation matches Attempt 103. The exact Attempt 103
environment was rerun, but all 53 Ollama requests failed to connect; no valid
comparison metrics were produced.

## Attempt 106: Pi-friendly reduced retrieval (blocked by runtime)

### Strategy and rationale

Test a Raspberry-Pi-oriented configuration by disabling pointwise reranking,
evidence-scope routing, and normalization stages. This reduces model loads and
LLM calls while retaining the standard compact prompt and default small BGE
embedding model.

### Exact configuration

```text
SCORER_POINTWISE_RERANK=false
EVIDENCE_SCOPE_ROUTING=
NORMALIZE_JOB_LOCATION=false
NORMALIZE_PREFERENCE_GUIDANCE=false
OLLAMA_MODEL=ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16
EVAL_OUTPUT_DIR=eval-results/pi-friendly-attempt-105
```

### Evaluation results

The run was started with `make eval-scorer`, but Ollama was unreachable. It
was interrupted after repeated connection failures; no valid predictions or
metrics were produced.

### Improvement/regression

No quality comparison is possible. The configuration is computationally
lighter, but runtime availability must be fixed before evaluating it.

### Conclusion and proposed next step

Retry when the Ollama service is running and accessible. On a Pi, use a local
quantized Qwen2.5 1.5B endpoint and verify the embedding backend separately.

### Retry result (2026-07-20)

An initial retry failed to connect, but a subsequent run completed successfully
when Ollama was available. The successful run produced exact accuracy `0.4528`
(24/53), N/A F1 `1.0`, and MAE `0.94`, with 0 errors. Mean latency was 2.71 s
(p95 3.80 s), about 15% faster than the baseline. The regression gate failed:
accuracy fell from 0.566 and MAE rose from 0.560. The lighter pipeline is
therefore faster but materially less accurate.

### Improvement/regression

Pending comparison with Attempt 103.

### Conclusion and proposed next step

Pending.
