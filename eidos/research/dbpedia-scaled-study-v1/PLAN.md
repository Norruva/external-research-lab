# Proposed larger EIDOS–LAYA comparison

This is one new offline experiment, currently unapproved and unexecuted. Source preparation and original authored fixtures do not consume a real-data slot. The launcher defaults to static closure verification and requires a separate authorization bound to this plan and its prepared source manifest. No authorization file is created during preparation.

## Question and frozen model

Does the new independently implemented sparse EIDOS recipe outperform the exact public zero-shot LAYA checkpoint on a larger, previously unscored eligible cohort from DBpedia14? This tests a new supervised artifact; it does not establish matched adaptation, a strongest possible LAYA configuration, JEV, universal recognition, or production readiness.

Use EIDOS source `e084549cbac649d16cdc7a0a437657b64521b932`, copied as a complete 27-file package snapshot. Fit exactly one `eidos.train_sparse` model using 1,000 training examples per label, 14,000 total. Fix three epochs, seed 113, learning rate 0.5, L2 0.0001 and the published inverse-square-root schedule. Training-only word/bigram/character TFIDF, zero initialization, float64 sparse fitting and checked float32 export remain unchanged. The final epoch is the sole primary artifact. Input/NNZ/vocabulary/work/time failures stop the attempt; no alternate recipe, subset, extra fit or rescue.

Reserve 100 development rows per label, 1,400 total, for diagnostic metrics only. Freeze the primary artifact before those diagnostics. Development accuracy, macro-F1, loss or confidence never selects a model, changes settings or decides whether test prediction proceeds. No calibration or acceptance policy is fitted; every EIDOS prediction remains review-only.

## Data and eligibility fixed before access

Reuse the two already cached public Parquet files for `fancyzhx/dbpedia_14` revision `9abd46cf7fc8b4c64290f26993c540b92aa145ac`. Exact file hashes, lengths and class counts are in plan.json. No acquisition, anonymous token, mirror, proxy change or installation is proposed. The isolated installed research PyArrow 26.0.0 reader is available only to corpus preparation, never EIDOS training/inference.

Validate every official train/test row, its schema, Unicode, source dimensions and class counts. The first pass counts token-normalized title, full feature text and content across both complete official splits. Eligibility requires all three keys to be globally unique. This deliberately restricts the estimand to eligible unique articles; it does not represent the complete original test population. Test covariates and class labels participate in this fixed curation; training vocabulary, IDF and weights use only selected training rows.

Reconstruct all 840 previously selected support/development/test-input rows from their neutral official split/ordinal IDs. Require their complete feature SHA256 to match the pinned prior input bytes, then derive actual title/content boundaries from the public source rows. The old input text and IDs are used only for exclusion. Never mount or read the old test gold, predictions, metrics or outcomes. Labels present in previous training/development input files are ignored by the exclusion parser.

Exclude prior selected IDs/components and enumerated historical exact full-feature/content fingerprints before ranking. All normalized-title/full-feature/content duplicates are already ineligible globally, including identical bodies with different titles. The historical manifest covers the same 40 public text sources as the prior preparation. This is an enumerated exact check, not proof of semantic/template/speaker or pretraining independence.

Among eligible rows, rank ascending by SHA256 of the fixed salt, newline and neutral source ID. Salt: `eidos-dbpedia-scaled-cohort-v1:2026-10-23`. Per label, official train supplies the first 1,000 ranked rows for training and the next 100 for development; official test supplies the first 100. Test totals 1,400 cases across all 14 labels. Feature text is complete title, newline and content; IDs are not model features. The two full source passes must have identical ordered validated-row hashes.

Selected text must fit the original 12,000-character per-case bound, 10 million training characters and one million each for development/test. Training output is capped at 32 MiB, other selected files at 4 MiB. Insufficient eligibility, a selected oversize/invalid row, bound or integrity failure rejects the entire attempt. No selected-row replacement, truncation, resampling or outcome-dependent exclusion. Eligibility exclusions and counts are reported. Singleton groups describe exact duplicate structure only, not independent semantic examples.

The source was already validated in the prior study; these are newly selected unscored cases, not a wholly unseen corpus. The recipe/protocol follows earlier outcomes on other cases and remains part of adaptive exploratory research. Old revealed evaluations and consumed budgets stay consumed.

## Reference and complete paired score

Use unchanged public LAYA source `ca5045a3f1fac06fc6daa5cf3371110f322f5255`, model revision `1c5edc17a7acd8701df6fc341c0d179f1c62c982`, weight SHA256 `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`. Verify complete dependency/source/model/legacy adapter trees, rights declarations and all 206 tensor payloads through the already reviewed adapter. No hosted TypeSafe/JEV service or proprietary extraction.

Offer all 14 fixed aliases/descriptions on every case, with the same encyclopedia prompt as the prior study. Use documented public `predict_long`, default window/stride, aggregate auto, batch one, English, one CPU inference/interop thread and float32. No shortlist, tournament, prompt selection, quantization, GPU, compilation or model/source modification. Before the first reference forward, verify all choices remain distinct and every input fits the predeclared full-document window bounds: at most 16 windows per case and 2,100 total. Exceeding a bound stops the attempt without partial quality scoring. Require actual window counts to match and reported truncation to be zero. Preserve the actual returned candidate when rounded probabilities tie; normalize only the documented rounded probabilities.

Only score after both complete 1,400-case streams and their receipts pass exact ID/text/input/config/model/reference checks. Primary metrics are candidate accuracy and fixed-14-label macro-F1. Compute 2,000 ordinary whole-group paired bootstrap replicates, seed 113, using the same fixed offered labels. Require BOTH left-minus-right individual approximate 95% percentile lower endpoints strictly above zero for the predefined descriptive numeric gate. A point estimate, macro-F1-only gain or accuracy tie is not a win. Intervals are not joint/global coverage or a power guarantee. Retain all negative, tied, failed-gate and aborted results; never promote a model automatically.

## Isolation and bounds

One unused output slot: `/workspace/eidos/evaluation/production/dbpedia-scaled-study-v1/v1`. No fallback, automatic retry, changed-boot recovery, resume, partial score, alternative model or resource reset. A new attempt would require a separately reviewed proposal and approval.

| Stage | CPU seconds per process | Elapsed seconds | Address space MiB |
| --- | ---: | ---: | ---: |
| Historical text/exposure preparation | 90 | 120 | 768 |
| Public corpus census and selection | 600 | 900 | 2,048 |
| Fixed owned training | 600 | 900 | 2,048 |
| Development diagnostics | 120 | 180 | 768 |
| EIDOS test prediction | 120 | 180 | 768 |
| LAYA test prediction | 4,800 | 7,200 | 4,096 |
| Complete paired scoring | 180 | 300 | 768 |

The proposed envelope is two planned CPU hours and three elapsed hours including preflight. Stage CPU caps sum to 6,510 seconds; a 300-second parent soft CPU reserve totals 6,810, leaving 390 unallocated within the 7,200-second proposal. The parent's hard CPU ceiling is 7,200 seconds so native children can set their approved caps; this is not an aggregate cgroup guarantee. Parent soft/hard address space is 768/4,096 MiB. File-size cap is 256 MiB. Existing training cooperative limits also apply.

All stages have separate network-isolated namespaces, clear environments, read-only pinned code/inputs, private fresh output, one expected refused route probe and no other IP connects. Preparation has no model; training has no development/test; diagnostics have no test; EIDOS prediction has no gold; LAYA has neither owned model/code nor peer outputs, training/development or gold; scoring has no weights, native packages or source case text. Temporary mounts expose only the declared public research artifacts. Parent hashes opaque data bytes but never parses new gold/text or quality to select a path. The same thread, memory, no-install/no-download/no-GPU controls apply throughout. No access to existing credentials, private repositories, VPNs, production services or personal host files.

## Limits that remain

EIDOS uses 1,000 labeled examples per class while LAYA is zero-shot. Exact article uniqueness does not prove semantic independence, and public-source/pretraining exposure is unknown. Dataset card CC-BY-SA 3.0/GFDL declarations remain caller-visible; public source text is not exclusive EIDOS property and dataset/model redistribution obligations remain unresolved. Keep selected cases, labels, predictions and models private. No confidence calibration, action authority, model activation, JEV result or production certification is created. `mission_complete=false`; `production_certified=false` unless future evidence separately resolves the relevant goals.

Approval is required by the user's original instruction to stop before installation/execution. Existing approvals concern different consumed sources, cases, attempts and budgets; they do not authorize this new experiment. Source, authored-fixture, REA identity and static-closure reviews must finish before the scoped approval question is presented.
