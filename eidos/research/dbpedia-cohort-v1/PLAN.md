# Proposed DBpedia14 acquisition and cohort preparation

This is a concrete **data-only proposal awaiting separate approval**. The prior
three-row PyArrow probe passed and consumed its approved slot. It does not
authorize this real-corpus run. The original user instruction requires approval
before installation or execution. No authorization file or execution slot for
this proposal has been created. The launcher defaults to static verification.

## Inputs and scope

Use the attached Codex Cloud Linux workspace only. Acquire two anonymous public
HTTPS files from `fancyzhx/dbpedia_14`, revision
`9abd46cf7fc8b4c64290f26993c540b92aa145ac`, through the existing proxy and TLS.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `dbpedia_14/train-00000-of-00001.parquet` | 106151899 | `0640e4664a99cc94c47db1d7b2e01c14455d5bbecb8183ad1f93bde59f3f28ee` |
| `dbpedia_14/test-00000-of-00001.parquet` | 13272475 | `05fed41640e97f93ffd442757f6a84170348cf0c7500ecbda9e95ddcd928c631` |

Both files together are 119424374 bytes, about 114 MiB. Their revision, sizes and
hashes came from public metadata; no real corpus download or parsing has occurred.
Only normal HTTPS redirects to `huggingface.co` and its subdomains or subdomains
of `hf.co` are allowed. No account credentials, cookies, explicit read-token/Xet
metadata endpoint, alternate mirror, automatic retry, proxy or TLS bypass. Signed
redirect queries are not written to receipts. A blocked request ends this attempt.

Reuse the already installed, isolated PyArrow26.0.0 reader, whose752files and
successful original probe receipt are pinned. No installation, application runtime
dependency, model fitting, prediction, scoring, MCP registration, GPU, paid compute,
TypeSafe/JEV/production service, private repository or existing credential access.
The public EIDOS application remains commit372f761; this research preparation does
not replace or activate any model or claim a new measured win.

## Selection frozen before acquisition

Use all14card-defined classes in their published ID order. Validate the full
official source:560000train rows,70000test rows,40000/5000per class; exact schema
`label:int64,title:string,content:string`. Reject nulls, noninteger labels,
malformed Unicode, extra columns, unexpected counts, title lengths outside1–2048,
content lengths outside1–64000, or more than300million characters per source.

Derive neutral row IDs from the pinned split and zero-based file ordinal. Rank
each class by SHA256 of the literal salt `eidos-dbpedia-cohort-v1:2026-10-23`, a
newline and its row ID. From official train take the first20ranks as support and
next20as development. Take the first20official-test ranks per class:280support,
280development,280test. IDs and source metadata are not classifier features.
Features are the entire title, newline and entire content. Selected text must
fit the existing app's12000characters/case,200000characters/role and2MiBfile
limits. No truncation, selected-row removal, replacement, alternate seed or
resampling after a failure. Invalid source rows reject the attempt even if they
would not have been selected.

The full official train scan rejects any selected-test title or full feature
appearing there under application-token normalization (NFKC, casefold, Unicode
word tokens, punctuation/underscores removed). Selected title/full-feature
connected components must stay within one role; normalized duplicate full
features within a role are refused. Test component IDs are sealed for future
whole-component paired inference. Same-title groups can have mixed labels and
unequal sizes; grouping is an exact-match check, not semantic independence.

## Historical overlap and role boundaries

`historical-source-manifest.json` pins40existing public-source files,16525119bytes:
processed SLURP (including unpredicted official test for fingerprinting only),
BANKING77, CLINC150, rejected MASSIVE, and the pinned public raw SNIPS train and
validation files. No answer-key, prediction or metric artifact is mounted.
Some public corpus rows contain original labels; the extractor reads only text,
never computes quality or refits an old model. This manifest is an enumerated
history check, not a proof that every prior text or pretraining corpus is covered.

The first offline namespace sees these historical files and source code only,
not the new corpus, reader or any weights. It exports SHA256 text fingerprints
under both whitespace and application-token normalization; no source text/labels
are exported. It limits files to32MiB each,128MiBtotal,200000rows and180000unique
fingerprints. The paired-surrogate CESU-8 handling for pinned SNIPS is lossless,
rejects unpaired surrogates and never ignores/replaces invalid bytes.

The second offline namespace sees only the pinned Parquet source, reader,
historical fingerprints and source/plan. Its native reader is explicitly local,
single CPU/IO worker,512row batches, no extension types, no memory mapping or
prebuffering, bounded Thrift/depth and checksum verification where provided.
Compare selected full features AND content-only fingerprints to the historical
index. Any overlap rejects the entire fixed cohort. Keep private support/dev,
gold-free test inputs, separate test labels, frozen groups, provenance and hashes;
do not print rows/labels or publish corpus material to GitHub. Files0400, private
directories0700; read-only mounts and empty homes/site-packages. These namespaces
are execution boundaries, not protection against a hostile same-UID host process.

## Limits and failure policy

One new exclusive slot: `evaluation/production/dbpedia-cohort-v1/v1`. No retries,
recovery fallback or resource extension is authorized by this proposal. Retain
partial downloads and failed evidence. No fitting/prediction follows readiness,
whether the cohort passes or fails.

| Process/stage | CPU seconds | Elapsed seconds | Address-space cap |
| --- | ---: | ---: | ---: |
| Parent/download/controller reserve | 180 | 900 for whole operation | 768MiBsoft,2GiBhard |
| Historical fingerprints | 90 | 120 | 768MiB |
| Native Parquet preparation | 180 | 300 | 2GiB |

Planned CPU limits sum to450seconds (7.5minutes) with parent reserve; these are
per-process controls, **not an aggregate cgroup guarantee**. Per-socket timeout
20seconds, whole-operation alarm900seconds, regular-file cap256MiB, one expected
refused network preflight per offline role and zero other IP connections. No
outbound access is available during source parsing. Failed limits or checks stop
the run and preserve its consumed attempt.

Before any execution, require a new approval receipt binding `plan.json` and
`prepared-manifest-v1.json` hashes to the actual user response. Both the launcher
and source closure are pinned; unlisted Python source additions are refused.
The reader's original approval cannot substitute for this receipt.

## What remains unresolved

The card declares CC-BY-SA3.0 and also describes GFDL. Public research processing
does not establish exclusive rights to source text; dataset/model redistribution,
share-alike and attribution obligations need review before distribution. Source
labels/near duplicates may be wrong; exact matching cannot establish semantic,
template, entity, speaker or pretraining independence. LAYA may have seen this
well-known public dataset during training. Cohort eligibility is unknown until
these checks actually run. No score or superiority claim follows preparation.

`future-study-intent.json` records the fixed proposed memory/GD/Adam candidate
recipes, development-only selection order and descriptive paired rule before
acquisition. The proposed EIDOS20supports/class versus zero-shot LAYA comparison
is asymmetric. A later concrete adapter/case/token feasibility and resource plan
needs separate approval before any real fit/reference execution. Existing revealed
benchmarks and all positive, negative and failed-gate results remain unchanged.
JEV is unmeasured. `mission_complete=false`; `production_certified=false`.
