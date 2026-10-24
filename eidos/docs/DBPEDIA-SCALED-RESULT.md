# EIDOS beats the pinned public LAYA configuration on the eligible DBpedia14 cohort

The single approved comparison completed successfully. The independently implemented sparse EIDOS model passed both predeclared descriptive superiority gates on the same 1,400 previously unscored eligible test articles. Both complete prediction streams were scored once; the independent terminal receipt review passed without new fitting, inference, metric computation or bootstrap runs.

| System | Correct / cases | Candidate accuracy | Fixed-14-label macro-F1 |
| --- | ---: | ---: | ---: |
| Fixed sparse EIDOS | 1,337 / 1,400 | 95.50% | 95.50% |
| Public zero-shot LAYA | 1,151 / 1,400 | 82.21% | 79.84% |

| EIDOS minus LAYA | Difference, percentage points | Individual approximate 95% paired percentile interval, percentage points | Predeclared gate |
| --- | ---: | ---: | --- |
| Candidate accuracy | +13.2857 | [+10.9982, +15.3571] | Pass |
| Macro-F1 | +15.6560 | [+13.7382, +17.4385] | Pass |

Both gates require their lower interval endpoints to be strictly positive. Both passed. This supports a cohort-scoped advantage for this supervised EIDOS artifact over this exact public zero-shot LAYA configuration. EIDOS used 1,000 labeled training examples per class; LAYA received the choices without dataset adaptation. The result does not establish matched adaptation or general superiority.

The score used 2,000 ordinary whole-group paired bootstrap replicates, seed 113, with the same fixed offered labels. All 1,400 declared exact-duplicate groups were singletons. The intervals are individual approximate descriptive intervals, not joint or project-wide coverage, a power guarantee, or proof of semantic exchangeability. Paired correctness counts were 1,119 both correct, 31 both wrong, 218 EIDOS-only correct and 32 LAYA-only correct. Exact unrounded aggregates are preserved in [the authoritative score](../evidence/dbpedia-scaled-study/score.json).

## The owned implementation and reference

Exactly one new original EIDOS model used the standard-library sparse trainer from public source `e084549cbac649d16cdc7a0a437657b64521b932`: 14,000 labeled training articles, three fixed shuffled SGD epochs, seed 113, learning rate 0.5, L2 penalty 0.0001 and an inverse-square-root learning-rate schedule. Vocabulary and TFIDF were fitted on training only. Fitting used float64 arithmetic and the checked float32 inference export. The final model was frozen before 1,400 development diagnostics; development did not select a model, setting, threshold or continuation path. All 27 core application package files remained unchanged for this experiment.

The owned artifact is `dbpedia_scaled_sparse`, version `1`, feature version `unicode-word-bigram-char345-tfidf-v2`, model JSON SHA256 `174bb6fcf4d302e90b2371217bccfbbee0ccb2c751e1a8bfa1735735e14d502c`. Inference uses the original locally controlled standard-library implementation and needs no pip/npm package, hosted model or third-party native inference runtime. Python 3.12+ on POSIX with standard SQLite/OpenSSL remains required by the application.

LAYA used public source `ca5045a3f1fac06fc6daa5cf3371110f322f5255`, checkpoint revision `1c5edc17a7acd8701df6fc341c0d179f1c62c982`, and weight SHA256 `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`. The reviewed adapter checked all 206 tensor payloads against the exact raw file; published source files remained unchanged. All 14 fixed descriptions and complete articles were offered through documented `predict_long`, using float32, batch one, English, default window/stride, aggregate auto and one CPU inference/interop thread. All 1,400 articles used one window, all choices remained distinct and reported truncation was zero. Returned candidates were preserved when rounded probabilities tied; only the documented rounded probabilities were renormalized. No shortlist, tournament, quantization or prompt selection was used.

## Cohort and independence limits

The source was the already cached public `fancyzhx/dbpedia_14` revision `9abd46cf7fc8b4c64290f26993c540b92aa145ac`. Two full passes validated all 560,000 official training rows and 70,000 official test rows, with no source change between passes. Selection required globally unique normalized title, full feature and content keys across both splits. Prior 840 selected inputs were reconstructed by source ID and full-feature hash; their IDs/components and the enumerated historical exact fingerprints were excluded before salted neutral-ID ranking.

Fixed quotas selected 1,000 training, 100 diagnostic development and 100 official-test articles per class. The 16,800 selected rows formed 16,800 singleton components, with zero declared cross-role exact overlap and zero selected historical exact overlap. No selected article was replaced, dropped, truncated, resampled or rescued. Complete title-plus-content text was used; source IDs were not features.

| Source split | Source rows | Excluded rows | Eligible rows per class |
| --- | ---: | ---: | ---: |
| Official training | 560,000 | 1,055 | 39,800–39,959 |
| Official test | 70,000 | 330 | 4,964–4,980 |

| Exclusion reason | Training rows | Test rows |
| --- | ---: | ---: |
| Historical full-feature/content fingerprint | 560 | 280 |
| Non-singleton content | 245 | 20 |
| Non-singleton full feature | 7 | 1 |
| Non-singleton title | 257 | 31 |
| Previously selected ID/component | 560 | 280 |

Reason counts overlap and must not be summed. The [completion review](../evidence/dbpedia-scaled-study/completion-review.json) retains all per-class eligibility aggregates.

This is adaptive exploratory research on a previously used source. Earlier outcomes informed the research direction, although these selected test cases were previously unscored. Fixed eligibility curation uses test covariates and labels and restricts the estimand to eligible unique articles. Exact uniqueness/exclusion does not prove semantic, template, speaker or pretraining independence. Supervised EIDOS versus zero-shot LAYA does not measure matched adaptation or the strongest possible LAYA configuration. The result supplies no JEV measurement, unseen-label, multilingual, out-of-scope, universal-recognition or 100% accuracy guarantee.

The earlier 280-case DBpedia comparison remains an accuracy tie at 243/280 each with both superiority gates failed. It was not rerun, rescored or overwritten. Historical positive, tied, failed-gate, leakage-rejected and aborted results remain separate evidence.

## Execution, ownership and remaining work

The complete seven-stage run took 1,560.3058 seconds elapsed. Recorded stage elapsed times were 97.3143 seconds for owned training, 2.8130 seconds for EIDOS test prediction and 1,366.5490 seconds for LAYA test prediction. LAYA recorded 1,357.3933 process CPU seconds and 2,815,976 KiB peak self RSS. These are stage measurements on this VM, including their respective setup; they are not general throughput or matched implementation-performance guarantees.

The approved envelope was one attempt, two planned CPU hours and three elapsed hours, with a maximum 4 GiB reference address space. Stage CPU caps totalled 6,510 seconds plus a 300-second parent soft reserve, leaving 390 seconds unallocated within the 7,200-second proposal. Limits were per process plus reserves, not an aggregate cgroup guarantee. All seven roles completed offline, each with one expected refused route probe and zero unexpected IP connections/socket creations. No retry, fallback, resume, partial score, alternative fit, old-budget reset, installation, download, GPU, paid compute, existing credential access, private service access or TypeSafe/JEV extraction occurred. This single attempt and selected test cohort are now consumed.

Candidate accuracy does not authorize automated decisions. The artifact remains uncalibrated and review-only: `value=null`, zero accepted coverage and no action authority. Its descriptive test ECE was 0.1546177; calibration and decision-policy validation remain work requiring separate appropriate data and evidence. No serving profile, acceptance policy or production deployment was activated by the study.

Real articles, gold, per-case predictions, vocabulary and weights remain private. Public text is not exclusive EIDOS property. CC-BY-SA 3.0/GFDL dataset and model attribution, share-alike and redistribution obligations remain unresolved. Private file modes do not establish privacy guarantees. The original implementation and owned model artifact are independently controlled; broader quality, privacy, rights, JEV comparison and production validation remain open. `mission_complete=false`; `production_certified=false`.

| Evidence | Binding |
| --- | --- |
| Approved plan | SHA256 `880284f8fc3aaadd3b07933a724abaa86b3c71392c738202d447d7365548302c` |
| Prepared source manifest | SHA256 `6405fa4f0e7ae7d43df931d079e8da365bbf54a6fddd7e0cf039d244a2a496e6` |
| [Complete score](../evidence/dbpedia-scaled-study/score.json) | SHA256 `871ff7afa7ac1a70635948250ebb60a07e96c11b5380576cf77478f57b1f34b6` |
| [Independent terminal review](../evidence/dbpedia-scaled-study/completion-review.json) | Frozen controls, opaque artifacts, complete-stream and aggregate-gate consistency; no refitting or rescoring |
| [Execution summary](../evidence/dbpedia-scaled-study/execution-summary.json) | Seven complete offline roles, resource limits and network receipts |
| [Frozen reviewed plan](../research/dbpedia-scaled-study-v1/PLAN.md) | Historical preparation snapshot; this report records the subsequent approved execution |

Passive REA byte-identity inspection binds only its enumerated members. It does not certify model quality, independence, security, rights or production readiness. Publication and any private packaging receipts are separate from the unchanged authoritative experiment score.
