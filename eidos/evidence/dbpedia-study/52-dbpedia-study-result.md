# DBpedia14 comparison completed: accuracy tied, superiority gates failed

The approved single study completed in the attached Codex Cloud Linux workspace
in **410.4665 elapsed seconds**. All seven roles
passed their frozen source/input/model, isolation and completion checks. The
user's “continue” approved exactly plan SHA256
`686b1ef74b5ed6defacb61031d4ab83de93f6d470629855d61c5ac0cda8e4d3d` and prepared source SHA256
`15e97a495e897d0be897bb75e067fca9d9187e355c7e0050754fe24912b3e39d`. The slot and its permission are consumed;
no retry, alternative test model, new score/bootstrap or automatic follow-up is
authorized. There was no new download, installation, GPU, paid compute,
TypeSafe/JEV service, model extraction, existing credential/private repository
access, proxy/TLS change or serving-model promotion.

## Actual result

| Model | Correct | Accuracy | Macro-F1, fixed 14 classes |
| --- | ---: | ---: | ---: |
| EIDOS stdlib Adam, frozen development-selected primary | 243/280 | 86.7857% | 86.6555% |
| Public zero-shot LAYA, all 14 choices | 243/280 | 86.7857% | 84.2196% |

EIDOS-minus-LAYA accuracy: **+0.0000 percentage points**,
individual approximate95%paired interval **[-4.6518, +4.2946] points**.
Macro-F1: **+2.4359 points**, interval **[-2.5585, +6.6237]**.
Both lower bounds include negative values. **Both predefined numeric gates failed.**
The point-estimate macro-F1 gain does not establish that EIDOS beats LAYA. No
candidate was substituted after test outcomes became available. The requested
superiority goal remains unmet on this new cohort; the earlier scoped wins and
negative/failed-gate results remain intact and do not override this result.

The predefined ordinary whole-component paired bootstrap ran once:1000draws,
seed113,280singleton components. All14offered classes count in macro-F1. Intervals
are individual approximate percentile intervals, not joint/global coverage or
verified population/source/pretraining independence. These test outcomes are now
revealed and consumed; do not refit, select another recipe, rescore or call a
repeat on these cases fresh evidence.

## What EIDOS now owns and controls

The study created three new private registries entirely through owned
standard-library code: memory, fixed12-epoch full-batch GD and fixed12-epoch
full-batch Adam. Each used20labeled supports/class (280total), fitting its own
TF-IDF vocabulary/IDF from supports only. No pretrained EIDOS weights, remote
inference or external training package was used. All three artifacts/receipts
passed independent file-size/hash and private0400mode checks. Model formats are
already supported by EIDOS's CLI, engine and service; this run tested the selected
model through the actual engine on every test input. The live service/registry
was not changed. Artifact provenance is declared research data, not certified
exclusive ownership of public source text or freedom from information leakage.

The private selected registry is
`evaluation/production/dbpedia-study-v1/v1/linear_adam/data/registry/`.
Its model identity is `ea9ccc9cd3d41a06a996b75a8b7cef474f07bb327376caa57ff99349d06be752`.
All models retain no calibration, disabled answer/action acceptance, human
review and no promotion. New app-independent model artifacts inherit no old
benchmark score. Original application source remains standard-library Python.

Development selection used only280official-train-derived development cases and
the exact pre-acquisition rule: greatest macro-F1, then accuracy, lower NLL,
then memory/GD/Adam listed order. Adam was selected for macro-F1 even though GD's
development accuracy was higher. The selector had no test input or gold. Both
prediction roles had no test gold. LAYA also had no support/development cases,
EIDOS weights, selection metrics or peer predictions. Scoring had no test text,
model weights or native reference runtime. The controller hashes gold as opaque
bytes for closure checking; only the scoring role parsed its labels.

## Reference fidelity and resources

Exact public LAYA source `ca5045a3f1fac06fc6daa5cf3371110f322f5255`, checkpoint
`convaiinnovations/laya@1c5edc17a7acd8701df6fc341c0d179f1c62c982`, weight SHA256
`891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`.
The reviewed pread loader checked all206tensor payloads against the file. Source,
dependency, model and rights inventories matched before native import. CPU,
float32, one inference/interop thread, eager mode, fast/compile disabled. No
weights, source, temperature, precision or candidate-order change occurred.

The public `predict_long` path was offered all14fixed natural-word choices on
every case. Preflight found14distinct option spans on every article. **All280
articles fit fully in one context window**; the public path therefore used280
ordinary full-state windows, with **zero truncated windows**. No document was
shortened for the comparison. Public returned choices, including rounded ties,
were retained. No shortlist/tournament or gold-conditioned choices were used.
The reference reported280accepted candidates, which is diagnostic classifier
state, not authority for a production action. EIDOS remains review-only.

Native process CPU through its receipt: **343.4719 seconds**;
peakRSS **2,814,492KiB** (2.6841GiB).
Whole elapsed includes wrappers, checking and all seven roles; CPU for other
roles/wrappers was not recorded as an aggregate. Do not treat elapsed as CPU.
The approved one-hour planned sum,90-minute elapsed alarm and4GiBnative address
space cap stayed unchanged. These are per-process limits/reserves, not an
aggregate cgroup guarantee. Each role had one expected refused network preflight
and zero other IP connections. Existing code/weights and source file pins stayed
unchanged. No partial prediction quality was scored.

## Evidence and next scientific step

Authoritative aggregate score SHA256:
`ec6a5e85a1311479098ec1f9fe4df16b248f2481fcb9da0fb3d9a50a6c910ef4`.
Existing score: `evaluation/production/dbpedia-study-v1/v1/score/data/result.json`.
Independent receipt review: `completion-review-v1.json`, created without fitting,
predicting, rerunning bootstrap or recomputing case quality. Per-case text,
labels, predictions, group mappings and real model binaries stay private.
Source preparation/16authored tests/eight fixture roles/52-member passive REA
inspection are documented in [the prepared-study report](51-dbpedia-study-prepared.md).
Those fixture checks used an explicit substitute and were never LAYA evidence.

The present20-support recipes do not establish superiority. The next technical
step is to improve owned training capacity and data efficiency, then freeze a
new evaluation disjoint from these revealed cases before measuring again. A
larger authorized training set and independently planned held-out cohort are
more defensible than changing the measured recipe or selecting on these test
outcomes. No new real experiment or resource extension is started by this report.

Dataset source: public DBpedia14 revision
`9abd46cf7fc8b4c64290f26993c540b92aa145ac`, Zhang/Zhao/LeCun2015. The card declares
CC-BY-SA3.0 and discusses GFDL. Attribution/share-alike and future data/model
redistribution obligations remain unresolved. Both classifiers may have source
or semantic relationships to other public corpora; LAYA pretraining exposure is
unknown. Supervised EIDOS versus zero-shot LAYA is asymmetric adaptation, not a
matched-training or strongest-LAYA comparison. JEV remains unmeasured. The
binomial count formula does not supply a semantic mechanism or100%guarantee.
`mission_complete=false`; `production_certified=false`.
