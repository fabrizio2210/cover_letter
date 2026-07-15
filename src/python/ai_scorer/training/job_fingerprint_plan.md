# Job-Fingerprint Dataset Isolation Plan

## Goal

Use a versioned job-description fingerprint as the only maintained job
identity. Split jobs before preference expansion, keep the 53 golden cases
external to training, preserve every paid label, and remove Mongo IDs from
schemas and generated artifacts.

## Fingerprint contract

- `jdfp:v1:description:<sha256>` identifies future jobs from the complete
  normalized description.
- `jdfp:v1:legacy-partial:<sha256>` identifies the current paid dataset from
  the normalized, deduplicated, deterministically sorted union of all stored
  relevant snippets for a legacy job. If no snippet exists, canonical title
  and location form the fallback payload.
- Fingerprint basis and version are explicit. A dataset run may not silently
  mix incompatible bases.

## Migration and reconciliation

1. Fingerprint all 53 golden cases from their complete embedded descriptions;
   preserve their labels and remove Mongo IDs from maintained provenance.
2. Regroup the existing 500 paid cases with the old ID only inside the
   one-time migration, compute one legacy-partial fingerprint per group, then
   remove the ID from migrated output.
3. Preserve case IDs, prompts, preferences, scores, availability, and the
   total label inventory exactly. Migration must make no Gemini calls.
4. Reconcile each legacy partial bundle against normalized golden
   descriptions:
   - all non-empty snippets contained in exactly one golden description means
     confirmed promotion overlap;
   - an empty bundle requires exact canonical title and location;
   - multiple, partial, or otherwise suspicious matches are quarantined;
   - no meaningful match remains eligible.
5. Confirmed and quarantined labels remain in the paid label inventory but are
   omitted from train and validation exports.

## Dataset construction

1. Future extraction fetches no Mongo ID, fingerprints and deduplicates raw
   job content, excludes golden fingerprints, samples eligible jobs, splits
   fingerprints into train and validation, and only then expands preferences.
2. The existing expanded dataset is migrated by splitting its unique eligible
   legacy fingerprints, then assigning every existing preference case from
   the persisted fingerprint split.
3. A split manifest records train, validation, confirmed promotion,
   quarantined, and golden fingerprints plus seed, ratio, fingerprint profile,
   preference-set hash, and golden hashes.
4. Export consumes the manifest rather than creating a new split.
5. Preflight and training startup fail on missing identities, cross-partition
   fingerprint overlap, promotion overlap, incompatible profiles, duplicate
   cases, or stale golden metadata.

## Paid-label conservation

Index reusable labels by fingerprint basis, fingerprint, preference key and
guidance, system prompt, and user prompt. Reuse only exact matches. Report
reuse/new/changed counts before any future paid labeling; migration and export
never call Gemini.

## Artifacts and observability

- Remove `source_job_id` from maintained Python schemas, canonical fixtures,
  proposed datasets, exported JSONL, summaries, preflight reports, manifests,
  tests, and documentation.
- Run manifests retain the dataset hash and exact fingerprint partitions.
- Keep only train and validation JSONL; the canonical 53-case suite remains
  the external validation/promotion gate.

## Verification

- Unit-test normalization, full and partial fingerprint stability, empty
  fallback, order-independent legacy aggregation, deterministic splitting,
  reconciliation outcomes, paid-label preservation, export enforcement,
  preflight overlap detection, and exact label reuse.
- Assert all 53 golden cases have fingerprints and no maintained artifact
  contains `source_job_id`.
- Report final eligible, confirmed, quarantined, train, and validation counts;
  do not buy replacement labels to reach a target row count.
