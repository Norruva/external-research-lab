# Compare two frozen owned classifiers on the same cases

`eidos.compare_owned` adds explicit `plan` and `score` stages to the [owned evaluation workflow](OWNED-EVALUATION.md). It compares two caller-trained review-only models using complete, paired predictions and whole caller-declared groups. It uses Python's standard library and performs no inference or fitting itself.

The current command supports the owned memory, gradient-descent and Adam registries accepted by `evaluate_owned`. It has no LAYA/JEV importer or service adapter. It provides reusable comparison mechanics for owned models; it does not create a new rival result.

## Run the authored demonstration

Train separate `owned-linear` and `owned-adam` registries using [LINEAR-TRAINING.md](LINEAR-TRAINING.md), with the same support/provenance files and logical demo model ID. Start from the repository's `eidos` directory with a private `evaluations` parent. All output paths below must be new.

First prepare the identical authored evaluation separately for each model:

```sh
for eidos_demo_model in owned-linear owned-adam; do
  python3 -S -B -m eidos.evaluate_owned prepare \
    --registry "owned-registry/models/$eidos_demo_model" \
    --support examples/owned-support-demo.jsonl \
    --support-provenance examples/owned-support-demo-provenance.json \
    --evaluation examples/owned-evaluation-demo.jsonl \
    --evaluation-provenance examples/owned-evaluation-demo-provenance.json \
    --output "evaluations/$eidos_demo_model-split"
done
```

Freeze the comparison before generating or examining its predictions:

```sh
python3 -S -B -m eidos.compare_owned plan \
  --left-plan evaluations/owned-adam-split/prediction-input/plan.json \
  --right-plan evaluations/owned-linear-split/prediction-input/plan.json \
  --groups examples/owned-evaluation-groups-demo.jsonl \
  --group-provenance examples/owned-evaluation-groups-demo-provenance.json \
  --output evaluations/paired-plan
```

Generate each model's gold-free predictions, then score the complete pair:

```sh
for eidos_demo_model in owned-linear owned-adam; do
  python3 -S -B -m eidos.evaluate_owned predict \
    --registry "owned-registry/models/$eidos_demo_model" \
    --inputs "evaluations/$eidos_demo_model-split/prediction-input" \
    --output "evaluations/$eidos_demo_model-predictions"
done
python3 -S -B -m eidos.compare_owned score \
  --comparison evaluations/paired-plan \
  --left-gold evaluations/owned-adam-split/gold-private \
  --right-gold evaluations/owned-linear-split/gold-private \
  --left-predictions evaluations/owned-adam-predictions \
  --right-predictions evaluations/owned-linear-predictions \
  --output evaluations/paired-result
```

The four original authored evaluation cases and arbitrary demonstration groups establish mechanics only. Two small illustrative groups cannot support a substantive quality claim. Do not select a method, epoch, grouping or seed after seeing these or any real evaluation outcomes. This demonstration does not determine whether Adam generalizes better than gradient descent.

## Paired scope and file roles

Planning requires distinct frozen model files but identical ordered case IDs, input-text hashes, offered labels, support/provenance hashes, code version, task and review-only policy. Both models must come from the owned evaluation workflow. All trained labels are offered together. Unknown gold labels remain refused. This is a closed-set candidate comparison, not an out-of-scope or unseen-label test.

The group input is UTF-8 JSONL with exactly `id` and `group_id`, both using the bounded ASCII identifier format used for cases. Every planned case must appear exactly once, with no extra cases. At least two nonempty groups are required. Mixed-label groups and unequal sizes are supported. Input order is canonicalized by case ID; original group-input bytes are also bound by hash. Use neutral identifiers and freeze group names, since the seeded implementation orders groups by name.

Supply a separate provenance JSON with exactly `source`, `license`, `citation`, `rights_basis`, as in the existing evaluation contract. It records a caller declaration rather than independently verified grouping, rights or independence. Construct groups from relevant speaker, source, template or connected-component relationships before examining outcomes. This command does not discover those relationships or validate group separation from training/development data; existing preparation checks exact support/evaluation ID and normalized-text overlap only.

The new private comparison directory contains `comparison.json`, exact copies of `left-plan.json` and `right-plan.json`, canonical `groups.jsonl`, and the exact `group-provenance.json`. It binds code, scope, group/provenance bytes, metrics, direction, resampling settings and numeric rule. Planning reads no gold or predictions and does no scoring. It cannot prove outcomes were previously unseen or prevent a caller from selecting a favorable protocol after earlier experiments.

Scoring needs that bundle, each model's bound gold, and both complete prediction directories. It needs no source case text or model weights and imports no inference model. It reuses the strict evaluation verifier: incomplete, duplicate, reordered, nonfinite, inconsistent or incorrectly bound predictions are refused even if their receipt is rehashed. Both gold streams and their declared evaluation source/provenance must match. Modified comparison plans/groups/code, unsupported settings and incomplete bundles are refused. The aggregate `result.json` contains no case/group IDs, texts, per-case predictions or bootstrap draws.

Portable commands implement logical file roles. Hard access separation requires external namespaces or access controls. Cloud verification ran five separate offline namespace stages: preparation, comparison planning without gold/predictions/models/text, two gold-free predictions, then paired scoring without models or source case text. Hashes detect inconsistencies in a trusted preparation workflow; they are not signatures or protection against full-chain forgery by a caller controlling every artifact.

## Statistics and interpretation

Differences are always **left minus right**, in fractions; multiply by 100 for percentage points. The report includes each arm's candidate accuracy and fixed-offered-label macro-F1, paired correctness counts, observed differences and individual approximate 95% percentile bootstrap intervals. Absent classes contribute zero to macro-F1, including in a resample.

For each resample, draw the original number of groups uniformly with replacement and apply the same multiplicities to both models. Every row of a selected group stays together. Unequal group sizes make the resampled case count vary. This is ordinary whole-group resampling, not stratification by gold class. Preaggregated per-group class counts avoid expanding repeatedly drawn large groups back into rows. The default is 1,000 replicates, seed 113. Explicit `--replicates` accepts 200–2,000 and `--seed` accepts an integer 0–2³²−1, both chosen before outcomes. Percentiles use linear interpolation at `(replicates−1) × percentile`.

The numeric rule passes only when **both** individual accuracy and macro-F1 lower bounds are strictly above zero. A zero lower bound fails. This descriptive rule is not automatic promotion, a probability that one model is better, or certification of superiority. Bootstrap coverage relies on unverified group sampling/exchangeability and adequate groups; small or dependent clusters can make intervals misleading. The two intervals are individual and approximate, with no joint/global coverage, repeated-selection or optional-stopping guarantee. No positive result repairs outcome-informed planning, contaminated data or adaptation differences. Retain all negative, failed-gate and failed-run evidence.

Both models retain zero accepted coverage and no action authority. Outcomes become revealed on scoring. New research claims require an independently designed, eligible cohort and frozen recipes. Previously revealed project benchmarks remain consumed; no historical quality scoring, resampling, refitting or LAYA/JEV experiment was repeated for this feature.

## Bounds and privacy

Existing evaluation limits still apply: at most 10,000 cases, 256 labels, 200,000 case/class values and bounded regular files with final symlinks refused. Comparison group input is at most 2 MiB and provenance at most 16,000 bytes. The additional conservative work estimate is `(replicates+1) × (8×cases + 6×labels + 3×groups)` and must fit 20 million units; some maximum-size cases will be refused. The same cooperative 60 process-CPU/90 elapsed-second phase checks apply. Use external process-tree limits for untrusted workloads; these are not aggregate cgroup or latency guarantees.

Outputs are new `0700` directories and `0400` files. Existing paths are refused; filesystem failure may leave incomplete new output. Private modes do not guarantee secrecy against hostile same-UID processes. Keep comparison bundles, gold, inputs and per-case predictions private; the documented `evaluations` directory is ignored by Git. Identical successful input bytes/code/settings reproduce identical numerical artifacts on the tested runtime; no cross-platform bit identity is promised.

`mission_complete=false`; `production_certified=false`. JEV remains unmeasured. TypeSafe/JEV service access and model extraction remain outside the authorized research scope.
