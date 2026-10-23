# Proposed independent DBpedia14 EIDOS–LAYA study

This is a new, concrete execution proposal. **Separate approval is required.**
The approved data-only operation completed successfully in 25.9835 seconds and
consumed its single slot. It authorized no real training, prediction or scoring.
No authorization file or real study slot exists for this proposal. Default
launcher mode checks inert file/tree hashes; it never imports a native model.

## Fixed source and eligible cohort

Use the attached Codex Cloud Linux workspace only. The pinned public
`fancyzhx/dbpedia_14@9abd46cf7fc8b4c64290f26993c540b92aa145ac` source has been
acquired through normal public HTTPS and validated: 560,000 train / 70,000 test
rows, 40,000 / 5,000 per class. The frozen hash-ranked cohort contains 280 supports,
280 development cases and 280 test cases across all 14 classes. All 840 selected
title/full-text components are singleton and stay within their role. The defined
exact historical/full-train overlap checks passed. No row was removed, replaced
or truncated. Selected text is the full title, newline and full content.

Readiness: `evaluation/production/dbpedia-cohort-v1/v1/completion-review-v1.json`.
Private inputs: `evaluation/production/dbpedia-cohort-v1/v1/corpus/data/`.
`config.json` pins every input size/hash, all 14 labels in sorted order, neutral
case IDs, fixed reference aliases and the task prompt. No fresh source, secret,
service, private repository, model artifact or dependency will be downloaded.
No PyArrow/other installation, npx, MCP registration, GPU or paid compute.

Exact matching is an eligibility check, not semantic, entity, paraphrase,
speaker or pretraining independence. The historical fingerprint index covers
84,246 rows in 40 enumerated existing public-source files, not every conceivable
prior exposure. DBpedia is well known; LAYA pretraining exposure is unknown.

## Seven roles and data boundaries

| Role | Inputs available | Output |
| --- | --- | --- |
| Memory train | 20 labeled supports/class, provenance, owned source | New private memory registry |
| GD train | Same supports/provenance and owned source | New private linear registry, fixed 12 epochs |
| Adam train | Same supports/provenance and owned source | New private linear registry, fixed 12 epochs |
| Development selection | Three complete registries and 280 development cases | Frozen primary artifact/selection receipt |
| EIDOS test prediction | Selected exact registry, selection receipt, gold-free test input | Complete 280-case numeric stream |
| LAYA test prediction | Exact public reference closure, gold-free test input and all 14 fixed choices | Complete 280-case numeric stream |
| Paired scoring | Two complete numeric streams/receipts, private gold/groups, selected identity | Aggregate metrics and paired intervals |

Training has no development/test files or old weights. Development selection has
no test inputs/gold/groups. Neither prediction role has gold; LAYA has no EIDOS
weights, support/development examples, selection metrics or peer predictions.
Scoring has no case text, model weights, native dependencies or historical source.
The controller hashes pinned files, including the gold file, as opaque bytes;
only the scoring role parses the test labels. No test outcomes exist yet.
Each role runs in a fresh offline bwrap namespace with immutable input mounts,
empty home/site-packages, cleared environment and dropped capabilities. Each has
one expected refused route probe and must have zero other IP connections.
Private new outputs, exclusive files, and per-role source/input pins are retained.
This is not protection from a hostile same-UID host process or a complete OS
supply-chain/security certification.

## EIDOS recipes and selection

The original pre-acquisition `dbpedia-cohort-v1/future-study-intent.json` specified
memory, 12-epoch GD and 12-epoch Adam, then development macro-F1, accuracy, lowest
negative log-likelihood and stable listed order. This proposal implements that
intent using existing public EIDOS source at commit
`372f76153b2cb949be8494592287113c22f5aff3`, frozen as a 26-file source tree.

Features/vocabulary/IDF come only from the 280 supports, using existing Unicode
word/bigram/character TF-IDF contracts and their bounds. Memory uses the existing
fixed top-five scoring recipe. GD uses learning rate 1 and L2 0.0001; Adam uses
0.12 and 0.00002, betas 0.9/0.999 and epsilon 1e-8, coupled weight L2 with
unpenalized bias. These are fixed full-batch recipes, not a new epoch or feature
search. Existing per-fit 250-million arithmetic-work and 60 CPU/90 elapsed
cooperative limits stay in force for the linear trainer, in addition to the
outer hard limits. A failed recipe aborts the study; do not select from fewer
recipes, change bounds or substitute the historical NumPy-trained winner.

Select exactly once on development, freeze all chosen artifact hashes before
any test prediction, and never refit or recalibrate on development/test. The
standard-library implementation and resulting artifacts remain independently
controlled. They inherit no old accuracy score. All models stay uncalibrated,
review-only with answer/action acceptance disabled. No registry promotion,
serving-application change or production action follows any result.

## Public LAYA reference and full-document behavior

Reuse the already reviewed offline public source
`ca5045a3f1fac06fc6daa5cf3371110f322f5255` and checkpoint
`convaiinnovations/laya@1c5edc17a7acd8701df6fc341c0d179f1c62c982`.
Weight SHA256:
`891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`.
Verify complete source, dependency, model and adapter manifests plus the existing
reviewed rights file before importing any native/reference package. Reuse the
reviewed pread loader, checking all 206 tensor payloads against the pinned file.
Keep original public source/weights unchanged. CPU, one inference/interop thread,
default float32, eager execution, fast/compile disabled, explicitly offline.

Each case offers all 14 choices using fixed snake-case aliases derived from the
published class names and matching natural-word descriptions. The prompt is
“Classify this encyclopedia article into one of the offered categories.” No
gold-conditioned candidate sampling, retrieval shortlist, tournament, calibration
fit, temperature override, per-case description choice or outcome-based routing.

Use the documented `Agent.predict_long`, English, default window and stride,
`aggregate='auto'`, `batch_size=1`, max_len512/head_max_len192. A fitting article
uses the public ordinary call; a longer one is scanned with overlapping token
windows, and the public choice aggregation takes the most-confident window.
This avoids deliberately limiting the reference to the first context window.
Document-window confidence is not calibrated document confidence. BPE window
decoding/re-tokenization may shift boundaries; the public algorithm's overlap
and actual usage/truncated-window counts are retained, not silently rewritten.
No custom aggregator or strongest-possible-reference claim is made.

Before the first reference forward, tokenize every test input and require all
14 option markers with 14 distinct option spans on every case. Verify documented
token budgets, at most 16 windows/case and 1,120 total planned windows. Any failure
rejects the fixed attempt before reference inference; no row drop or adaptive
window setting. Require actual window counts to match preflight. Use the actual
returned choice, including rounded probability ties; do not reconstruct it by
argmax. Public four-decimal probabilities are only renormalized by the reviewed
decoder. Reference abstentions remain in forced-choice accuracy/macro-F1; their
answer-state count is diagnostic, not operational authority.

## One sealed comparison and descriptive decision rule

Only after both full 280-case streams pass their exact config/source/model/input
hash and ordered-ID/text-fingerprint checks may scoring read test gold. Refuse
partial, reordered, duplicated, altered, wrong-checkpoint or wrong-API artifacts.
All 14 classes count in fixed-label macro-F1. Compute selected EIDOS versus LAYA
accuracy and macro-F1, paired correctness counts and 1,000 ordinary whole-group
paired bootstrap resamples with seed113. Use both sides' same component draw and
linear percentile intervals at 2.5%/97.5%. The descriptive numeric rule passes
only when **both individual lower bounds exceed zero**. No per-case/group IDs,
resample draws or test text are placed in the aggregate report/publication.

EIDOS has labeled supports; LAYA is zero-shot here. This is asymmetric adaptation,
not a matched-training or strongest-LAYA comparison. Intervals are approximate
individual descriptive intervals, not joint/global coverage, an untouched source
guarantee or a production certificate. The earlier binomial equation describes
IID success counts; it supplies no semantic mechanism or near-100% guarantee.
All old revealed benchmark runs are consumed and unchanged. JEV is unmeasured;
no TypeSafe/JEV service access or model extraction is part of this study.

## Resource ceilings and failure policy

One new slot: `evaluation/production/dbpedia-study-v1/v1`. No retry, fallback,
resume, resource extension, partial scoring, test-informed tuning, candidate
replacement or automatic next experiment. Preserve all completed/failed evidence.

| Role | CPU seconds | Elapsed seconds | Address-space cap |
| --- | ---: | ---: | ---: |
| Each of three training roles | 90 | 120 | 768 MiB |
| Development selection | 90 | 120 | 768 MiB |
| EIDOS prediction | 60 | 90 | 768 MiB |
| Native LAYA preflight and prediction | 2760 | 4200 | 4 GiB |
| Paired scoring | 120 | 180 | 768 MiB |
| Parent/controller reserve | 300 soft / 3600 hard | Whole operation 5400 | 768 MiB soft / 4 GiB hard |

Planned stage CPU limits plus the parent reserve sum to 3,600 seconds (one CPU
hour); whole-operation elapsed alarm is 90 minutes including static preflight.
These are per-process limits and reserves, **not an aggregate cgroup guarantee**.
The native duration is a conservative budget, not a measured new-domain estimate.
Reference memory/runtime feasibility on these actual cases is unresolved until
execution. No GPU/paid compute/install/download or external network request.

## Validation and approval boundary

Sixteen authored test methods cover numeric/policy/file contracts, development
tiebreaks, complete pairing, returned rounded ties, long-document controller
window enumeration, abort-before-forward choice/window checks and rejection of
wrong checkpoint/API/window/selected-model receipts. The final authored-fixture
checkpoint ran unit checks plus all seven roles in separate offline namespaces,
with actual stdlib memory/GD/Adam fits, selection, prediction and paired scoring.
Its reference was explicitly an authored substitute; it never loaded LAYA,
DBpedia, PyArrow or any native reference dependency. This validates mechanics,
not real classifier quality or native model feasibility. Earlier fixture
checkpoints remain preserved.

The launcher defaults to static-only. Before executing it, bind a new actual user
reply to this plan and the prepared source-manifest hashes. The original user
instruction is **“Stop and request approval before any installation or execution.”**
The successful reader/data-only authorizations do not authorize this larger scope.
Do not create an approval receipt until that reply arrives.

Dataset CC-BY-SA3.0/GFDL attribution, share-alike and future artifact redistribution
obligations remain unresolved. Keep data, new models, private gold and per-case
predictions out of GitHub. Public source ownership is not exclusive ownership of
the public training text. Neither a positive gate nor a completed run establishes
general superiority, JEV superiority, universal recognition, 100% reliability or
production readiness. `mission_complete=false`; `production_certified=false`.
