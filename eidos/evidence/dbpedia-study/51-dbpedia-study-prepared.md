# Real independent EIDOS–LAYA study source is prepared

The approved DBpedia data-only operation completed successfully; see
[its completion report](50-dbpedia-cohort-complete.md). Both anonymous pinned
public downloads worked through normal proxy/TLS. All 630,000 source rows
validated, producing the fixed 280 support / 280 development / 280 test cohort
with no defined cross-role or historical exact overlap and no row replacement.
That one-attempt approval is consumed. No real DBpedia model fit, prediction or
test quality score exists yet.

This continuation builds the complete next study controls, freezes the actual
eligible cohort and existing public native closure, verifies original authored
fixtures and uses pinned REA for passive source-packet inspection. It does not
start the next real experiment. Public application commit
`372f76153b2cb949be8494592287113c22f5aff3` remains unchanged in open PR #1; research
preparation/evidence is in the cloud workspace. Neither corpus material nor new
weights/gold/per-case predictions have been published.

## Implemented behavior

`research/dbpedia-study-v1/contracts.py` enforces bounded regular files, strict
JSON, exclusive private outputs, exact row IDs/counts, finite normalized numeric
distributions, review-only owned predictions, complete paired text hashes and
frozen development tiebreaks. LAYA's actual returned choice is preserved when
published probabilities round to a tie. Reconstructing that choice by argmax
would change its public behavior and is explicitly avoided.

`study.py` implements seven real roles: owned memory, fixed 12-epoch GD, fixed
12-epoch Adam, development-only selection, selected EIDOS prediction, public
LAYA prediction and numeric-only paired scoring. The three recipes and selection
order implement the intent recorded before corpus acquisition. Vocabulary/IDF
fit support text only; no new hyperparameter/epoch search, calibration or refit.
The selector freezes exact artifact identities before any test prediction.
Any failed recipe aborts the study rather than reducing the candidate set.
New models retain disabled acceptance/actions and are never promoted.

The reference path uses the exact existing public LAYA source/checkpoint and
already reviewed tensor-checked pread loader, CPU float32, one inference/interop
thread, eager execution and fast/compile disabled. All 14 fixed classes are
offered on every case, through fixed natural-word aliases. No retrieval shortlist,
tournament, gold-conditioned choices, adaptive description choice or temperature
override. Complete source/dependency/model/rights inventories are checked before
native import. No installation or additional download is required.

Static review of public `laya/agent.py:1601` identified `predict_long` as the
documented path for reading an article beyond one context window. The new
reference control uses its default window/stride, `aggregate='auto'`, batch1 and
English. A fitting article uses the ordinary call; longer articles use overlapping
windows and the public most-confident-window choice aggregation. It retains
public usage/window/truncation diagnostics. Document-window confidence is not a
calibrated probability for the full document, and decode/re-tokenization can
shift boundaries. This uses public behavior rather than a custom aggregator.

Before the first reference forward, preflight every case for all14distinct
option spans, documented512/head192budgets, at most16windows/case and1120total
windows. All actual window counts must match. A failed check stops the fixed
attempt; no case drops or alternate settings. This actual native long-document
path and its domain/runtime feasibility are still unmeasured; authored mocks
cannot establish real model compatibility or accuracy.

Scoring requires both complete bound streams, matching ordered IDs/text hashes,
selected-model receipt and the exact reference checkpoint/source/API/window
contract. It reads gold only after both predictions succeed. The controller hashes
gold as opaque bytes during closure checks; prediction/training roles never see
its file. Compute all14-class accuracy/macro-F1 and1000whole-component paired
bootstrap resamples, seed113. Both individual approximate95%lower bounds for
EIDOS-minus-LAYA must exceed zero for the descriptive numeric rule. No partial
quality scoring, per-case outcome publication, new experiment or promotion follows.

## Verification

The final authored-fixture checkpoint passed **16 unit test methods and all eight
offline roles** (unit checks plus seven workflow roles) in **3.8371 seconds**.
It used actual standard-library memory/GD/Adam fitting, development selection,
review-only engine inference and paired scoring on original Alpha/Beta fixtures.
The reference was explicitly an authored substitute, never actual LAYA. The final
score marks this fixture scope explicitly, preventing it from becoming a LAYA
quality claim. No DBpedia, PyArrow, real native package/model or credential was
mounted in these tests.

Tests cover independent numeric/file/policy contracts, fixed selection tiebreaks,
partial/reordered/duplicate/text-tampered pairs, strict JSON/file bounds,
rounded reference ties, independently enumerated short/four-window controller
behavior, abort-before-forward choice collapse/window bounds, and refusal of
wrong checkpoint/API/window or changed selected-model receipts. Expected failure
categories are checked before scoring. Each of eight namespaces had one expected
refused preflight and zero other IP connections. All five control-source files
stayed unchanged. Earlier fixture checkpoints are retained. The whole authored
check had a120second alarm,180CPUparent/per-process limits and768MiBaddress space;
these are not an aggregate cgroup guarantee.

The real launcher `run_dbpedia_study_v1.py` passed default static verification of
30source/input/metadata pins, five complete trees, existing reviewed rights and
CPython executable/libpython pins. Its prepared owned source tree has26members.
No native/model package is imported by static verification; the reused tree/rights
verifier is standard-library code. Actual corpus/gold bytes are hashed, not parsed
for model selection or quality. Existing OS/standard-library mounts are not an
independently certified full software supply chain.

## REA inspection

Pinned REA at `e09a503f44fba1639b71907a018d71161360f5ae` passively inspected the
exact52-member source/configuration/manifest/readiness/fixture-evidence packet.
Every member hash and size matched, with53graph nodes, complete container-entry
coverage and zero integrity contradictions. Elapsed10.2104seconds; tooling's
25132-entry inventory and Node pin stayed unchanged. One expected refused
preflight, zero other IP connections, no archive-member import/execution.
The packet excludes actual corpus rows, gold, per-case predictions, model weights
and native binaries. It includes their inert hash inventories only. This is
integrity evidence, not security, rights, semantic independence or quality proof.

Kit `eidos-dbpedia-study-review-v1.tar.gz`:1084358bytes; SHA256
`0e00ccc5f1ffed4ed8d1d7c53e19ea3c06939daf29a16ea6b6e1b03ba0cf1c7b`.
Internal manifest SHA256
`3b48e5fb0390c90e7ec11c51e571b005ff4876e4754ea1231293a7dd6b01f647`.
REA evidence `ev_81555b73043bf83a2da86dd8cfe5586f826f458e9c291e4ff3a3d0fded9c9460`;
stdout SHA256
`d36ba0bf8d99966e3ecef3cf4fbe103ab48207d3c1014385cec6d477328ed8c2`.
Records: `evaluation/production/dbpedia-study-v1/authored-fixture-v3/` and
`evaluation/production/dbpedia-study-v1/rea-static-integrity-review-v1.json`.

## Exact proposed next operation

[Full execution plan](dbpedia-study-v1/PLAN.md), scope
`eidos-dbpedia-independent-study-v1`.
Plan SHA256 `686b1ef74b5ed6defacb61031d4ab83de93f6d470629855d61c5ac0cda8e4d3d`.
Prepared source SHA256
`15e97a495e897d0be897bb75e067fca9d9187e355c7e0050754fe24912b3e39d`.

One new slot, three fixed real EIDOS fits, development-only selection, complete
280-case EIDOS/public-LAYA prediction and one paired score. Planned CPU limits
sum to **one CPU hour**, with **90 minutes elapsed including preflight**, one
native inference thread and **4GiB** maximum reference address space. Per-process
limits/reserves, not an aggregate cgroup guarantee. Native preflight/inference is
capped at2760CPU/4200elapsedseconds; other role limits are explicit in the plan.
No GPU/paid compute, download, installation, credential/private repository/service
access, TypeSafe/JEV/model extraction, retry, fallback, drop, resume or extension.

No authorization file or real-study slot exists. The original user instruction
**“Stop and request approval before any installation or execution”** requires
separate approval for this training/reference scope. The completed reader and
data-preparation permissions cannot substitute for it. The launcher remains
static-only until an actual user reply is bound to both exact hashes.

## Assessment limits

No real training/reference quality result is available from this continuation.
EIDOS has20labeled supports/class while LAYA is zero-shot here, an asymmetric
comparison. Public DBpedia pretraining exposure, semantic/entity/speaker/template
independence and generalization remain unknown. Individual bootstrap intervals
do not establish joint/global coverage, general superiority or production safety.
The binomial count equation supplies no semantic mechanism or100%guarantee.

Dataset CC-BY-SA3.0/GFDL attribution/share-alike and future artifact distribution
rights remain unresolved. Research artifacts stay private; code/model control
does not imply exclusive ownership of public training text. All earlier positive,
negative, failed-gate and leakage-rejected evidence remains unchanged and consumed.
JEV is unmeasured. No model or acceptance policy is activated. The new source
is cloud research preparation, not a newly published application or product
certification. `mission_complete=false`; `production_certified=false`.
