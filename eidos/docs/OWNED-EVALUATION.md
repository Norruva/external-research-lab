# Evaluate an owned classifier on separate labeled data

`eidos.evaluate_owned` measures an existing owned model without fitting, calibrating, selecting thresholds, activating a registry, or calling a reference service. It uses Python's standard library. Its three explicit stages separate case preparation, prediction, and aggregate scoring.

Use a single-model registry produced by `eidos.train_support` or `eidos.train_linear_support`, including its unchanged training receipt. Other historical/preinstalled models and calibrated profiles are outside this command's current scope. All trained labels are offered together, in English, with the existing review-only policy. Unknown gold labels are refused; this is closed-set evaluation, not an out-of-scope detector test.

## Run the authored demonstration

First train `owned-registry/models/owned-linear` as described in [LINEAR-TRAINING.md](LINEAR-TRAINING.md). From the repository's `eidos` directory:

```sh
mkdir -m 700 evaluations
python3 -S -B -m eidos.evaluate_owned prepare \
  --registry owned-registry/models/owned-linear \
  --support examples/owned-support-demo.jsonl \
  --support-provenance examples/owned-support-demo-provenance.json \
  --evaluation examples/owned-evaluation-demo.jsonl \
  --evaluation-provenance examples/owned-evaluation-demo-provenance.json \
  --output evaluations/demo-split
python3 -S -B -m eidos.evaluate_owned predict \
  --registry owned-registry/models/owned-linear \
  --inputs evaluations/demo-split/prediction-input \
  --output evaluations/demo-predictions
python3 -S -B -m eidos.evaluate_owned score \
  --plan evaluations/demo-split/prediction-input/plan.json \
  --gold evaluations/demo-split/gold-private \
  --predictions evaluations/demo-predictions \
  --output evaluations/demo-score
```

Use new output names for a new explicitly planned run. Existing paths are refused and never cleaned up or overwritten. The four authored evaluation examples and the authored training supports demonstrate mechanics only. Their scores are not independent semantic-quality, LAYA, JEV or production evidence. The memory registry works through the same commands when explicitly selected with its matching support files.

For your own data, evaluation JSONL rows have exactly `id`, `text`, `label`. IDs use the same bounded ASCII identifier format as training. Labels must already belong to the model. Provide a separate provenance JSON with exactly `source`, `license`, `citation`, `rights_basis`, each nonblank and at most 1,000 characters. Declarations are caller-supplied; rights and ground truth are not independently verified. Use neutral case IDs, since semantic identifiers can themselves disclose labels.

The support/provenance bytes must exactly match the model's training lineage, including their hashes. Preparation refuses support/evaluation ID overlap, identical normalized word sequences across those roles, duplicate evaluation IDs or normalized text, unknown labels, malformed/extra JSON fields and blank rows. Evaluation need not be balanced or cover every trained class. Nothing is silently dropped, selected, or truncated.

## File roles and integrity

Preparation writes a new private bundle:

```text
prediction-input/plan.json       # code/model/data identities, labels and case IDs
prediction-input/inputs.jsonl    # case IDs and text, no gold field
gold-private/binding.json        # gold/plan/source/provenance bindings
gold-private/gold.jsonl          # case IDs and labels, no source text
```

Prediction reads the planned text and bound registry, calls the real Engine, and writes only case IDs, candidate labels, logits, probabilities and review status, plus a completion receipt. It reads no gold input and requires a complete review-only distribution for every case. Trace IDs, timestamps and execution timing are excluded from persistent numeric output.

Scoring needs only `plan.json`, gold and completed predictions. It reads no source case text, support file or model weights and imports no inference model. It refuses missing, duplicate or reordered cases, hash/receipt/model/plan/code mismatches, inconsistent probabilities/logits/candidate labels and an enabled action/acceptance policy. Rehashing a partial prediction stream does not bypass completeness checks. Its output is aggregate `result.json`, with no per-case rows.

These are logical input contracts in the portable command. They do not create an operating-system sandbox automatically. Hard access separation requires your own process/container mounts or access controls. The cloud verification ran each CLI stage in a separate offline, read-only namespace: prediction had no gold files; scoring had neither registry weights nor source case text. That verifies operation with those inputs absent, not arbitrary-host security.

SHA-256 bindings detect inconsistent artifacts under a trusted preparation workflow; they are not signatures, data-rights proofs, trusted ground truth, or protection against a caller who deliberately rewrites an entire manifest chain. All package Python/schema files are pinned. Code changes require the matching versioned checkout to reproduce an old plan; the tool refuses silently switching implementations.

## Metrics and limitations

The report distinguishes **candidate accuracy** from accepted answers. It includes a fixed-offered-label macro-F1 (absent classes contribute zero), support-weighted F1, per-class precision/recall/F1, confusion counts, stable logit-based negative log-likelihood, multiclass Brier sum, ten equal-width confidence bins and descriptive ECE. Brier is summed over classes per case, then averaged over cases. Confidence 1 belongs to the final bin.

All supported registries have disabled acceptance gates: accepted coverage is zero and selective accuracy is null. Candidate probabilities remain uncalibrated. These descriptive metrics do not fit a calibration policy or authorize actions. No confidence intervals, population guarantees, model promotion or threshold selection are produced.

Exact ID/text separation does not establish template, speaker, source, paraphrase, semantic or untouched-holdout independence. The tool cannot know every prior use of a dataset or prevent outcome-informed model selection. Evaluation outcomes become revealed when scored. Choose and freeze training/evaluation roles, recipe, candidate inventory, metrics and comparison protocol before examining outcomes; retain all failed/negative results. Existing revealed project benchmarks remain consumed and must not be called fresh or reused to select a later recipe.

## Bounds and privacy

Input files are bounded regular files with final-component symlinks refused. Labeled files are at most 2 MiB; evaluation allows 1–10,000 cases, 12,000 text characters per case and 200,000 total. At most 200,000 case/class values and a conservative 250-million-unit prediction-work estimate are supported; some maximum-size workloads will be refused. Prediction output is capped at 16 MiB. Each phase cooperatively checks 60 process CPU seconds and 90 elapsed seconds. Use external resource limits for process-tree or hostile workloads; these checks are not aggregate cgroup guarantees or latency claims.

Each output root and private subdirectory is `0700`; files are `0400`. Filesystem failure may leave a new incomplete directory. Never activate or publish incomplete artifacts. Repeated successful runs with identical files/model/code on the tested runtime reproduce identical artifacts; no cross-platform bit identity is promised. Keep evaluation inputs, gold and per-case outputs private. The documented `evaluations/` directory is ignored by Git, but arbitrary output paths are your responsibility. Vocabulary/model artifacts may reveal support information; private modes are not a hostile same-UID or differential-privacy guarantee.

No hosted model, external resolver, installation, existing credential, TypeSafe/JEV service or extraction is used. This workflow creates no new LAYA/JEV superiority result. `mission_complete=false`; `production_certified=false`.
