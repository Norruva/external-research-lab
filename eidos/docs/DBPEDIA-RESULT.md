# DBpedia14: accuracy tied; superiority gates failed

This separately approved comparison ran once on 280 cases across all 14 DBpedia
classes. It evaluated the actual owned standard-library Adam trainer, fixed at
12 epochs and selected using separate development data, against the pinned public
zero-shot LAYA checkpoint. No per-case input, gold, prediction or model binary is
published here.

| Model | Correct | Accuracy | Fixed 14-class macro-F1 |
| --- | ---: | ---: | ---: |
| EIDOS, frozen development-selected Adam | 243/280 | 86.7857% | 86.6555% |
| Public LAYA, all choices and full article | 243/280 | 86.7857% | 84.2196% |

EIDOS-minus-LAYA accuracy was **+0.0000 percentage points**,
individual approximate 95% paired bootstrap interval **[-4.6518, +4.2946]**.
Macro-F1 difference was **+2.4359 points**, interval
**[-2.5585, +6.6237]**. Both lower bounds include negative values. **Both predefined
numeric gates failed. This study does not establish that EIDOS beats LAYA.**
The macro-F1 point estimate is higher; accuracy is exactly tied. No alternative
model was substituted after test outcomes became available.

## Training, data and isolation

The frozen, hash-ranked cohort used 20 supports and 20 separate development cases
per class from official train, and 20 test cases/class from official test.
The data-only predecessor validated all 560,000 train / 70,000 test source rows.
The selected 840 title/full-text components were singleton with no cross-role
component, selected-test title/full-text overlap with official train, or defined
selected full-feature/content-only overlap with 84,246 enumerated historical
public-source rows. No selected row was removed, replaced, resampled or truncated.
Exact checks do not prove semantic, paraphrase, entity, speaker or pretraining
independence. LAYA's exposure to this well-known public dataset is unknown.

Three owned recipes fitted supports only: memory, fixed 12-epoch GD and fixed
12-epoch Adam. Vocabulary and IDF came only from supports. Selection used highest
development macro-F1, then accuracy, lower NLL and fixed memory/GD/Adam order.
Adam won the macro-F1 rule even though GD had higher development accuracy.
All three registries and receipts are independently controlled private artifacts.
The chosen model was frozen before any test prediction. There was no refit,
calibration fit, old-model substitution, new dependency or hosted EIDOS inference.
All new EIDOS models remain uncalibrated, review-only with action/answer acceptance
disabled; no model was promoted or activated in the serving application.

Training had no development/test files. Selection had no test input/gold. Both
prediction roles had no gold. LAYA also had no EIDOS weights, examples, selection
metrics or peer outputs. Scoring had no case text, model weights or native
reference runtime. The controller hashes gold as opaque bytes; only scoring
parses test labels. Every role used its own offline read-only-input namespace,
with one expected refused preflight and zero other IP connections.

## Public reference and statistics

LAYA used exact public source `ca5045a3f1fac06fc6daa5cf3371110f322f5255`, checkpoint
`convaiinnovations/laya@1c5edc17a7acd8701df6fc341c0d179f1c62c982`, weight SHA256
`891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`.
Complete source/dependency/model/rights manifests were checked before native
import. The reviewed pread loader checked all 206 tensor payloads. One CPU
inference/interop thread, float32, eager execution, fast/compile disabled; no
source, tensor, temperature or precision changes.

All 14 fixed natural-word choices were offered on every case. The public
`predict_long` API used its default window/stride, automatic aggregation, batch1
and English. **All 280 articles fit entirely in one window**, so the public
ordinary full-state path ran 280 windows with zero truncation. All option spans
were distinct; no retrieval shortlist, tournament or gold-conditioned selection.
The actual public choice was retained, including rounded probability ties.
Its answer-state count is diagnostic, not production action authority.

One ordinary whole-component paired bootstrap ran: 1,000 draws, seed113,
280 singleton components, all 14 labels counted in macro-F1. Both individual
approximate lower bounds had to exceed zero. These are descriptive individual
intervals, not joint/global coverage, a confirmatory independence guarantee or a
production certificate. EIDOS has labeled supports while LAYA is zero-shot;
this is asymmetric adaptation, not matched training or strongest-LAYA evidence.
These test outcomes are revealed and consumed. Do not select/refit another model,
rerun scoring/bootstrap or call these cases a fresh future evaluation.

Native CPU was 343.4719 seconds; peakRSS
2,814,492 KiB. Whole study elapsed was
410.4665 seconds. Other roles/wrappers were
not recorded as aggregate CPU. Limits remained one planned CPU hour with reserve,
90 minutes elapsed and 4 GiB native address space, using per-process controls,
not an aggregate cgroup guarantee. No GPU, paid compute, installation, download,
existing credential, private service, TypeSafe/JEV access or extraction.

## Evidence and next work

[Aggregate score](../evidence/dbpedia-study/score.json) ·
[Independent completed-receipt review](../evidence/dbpedia-study/completion-review.json) ·
[Development selection](../evidence/dbpedia-study/development-selection.json) ·
[Role/resource receipts](../evidence/dbpedia-study/execution-summary.json) ·
[Readiness](../evidence/dbpedia-study/cohort-completion-review.json) ·
[Exact historical source and plan](../research/dbpedia-study-v1/README.md).

Score SHA256: `ec6a5e85a1311479098ec1f9fe4df16b248f2481fcb9da0fb3d9a50a6c910ef4`.
The review verifies completed identities/aggregate consistency without another
fit, prediction, score or bootstrap. Sixteen authored tests and eight isolated
fixture roles validated the controls; their reference was explicitly a substitute,
never LAYA quality evidence. Pinned REA passively matched all 52 prepared-source
packet members. Artifact identity inspection does not certify security, rights,
independence or quality. Later publication inspection is recorded separately.

The next engineering need is stronger owned training capacity/data efficiency
and a newly sealed evaluation. The portable trainer currently requires exactly
20 supports/class. A larger supervised regime needs a new bounded training/data
contract, not selecting another model on these revealed cases. No next real
experiment or resource extension is authorized by this consumed study.

Dataset: `fancyzhx/dbpedia_14@9abd46cf7fc8b4c64290f26993c540b92aa145ac`;
Zhang, Zhao and LeCun (2015), Character-level Convolutional Networks for Text
Classification. Its card declares CC-BY-SA3.0 and describes GFDL. Attribution,
share-alike and future data/model redistribution obligations remain unresolved.
Public source text is not exclusively owned by EIDOS. All earlier positive,
negative, failed-gate and rejected-leakage results stay visible. JEV remains
unmeasured; general superiority, universal recognition, 100% reliability and
production certification remain unsupported. `mission_complete=false`;
`production_certified=false`.
