# Build a classifier from your own labeled examples

This optional CLI builds a NEW review-only support-memory registry using Python's standard library. It reads only the two files you explicitly supply; no network, pretrained model, package installation or hosted service. Existing registries/configurations are never activated or changed.

Supply UTF-8 JSONL with exactly these fields per row:

```json
{"id":"invoice-01","text":"Please explain this invoice","label":"billing_help"}
```

Use exactly20distinct examples per class,2–256classes. IDs/labels/model IDs use1–128ASCII letters,digits,dot,underscore or hyphen, starting with a letter/digit. Texts are at most12,000characters; total at most200,000characters; input at most2MiB. Duplicate IDs and duplicate normalized word sequences are refused across all classes. Blank lines, unknown fields, duplicate JSON keys, malformed Unicode, empty word sequences and unbalanced quotas are refused rather than silently cleaned/selected. The caller chooses and reviews the supports; this command does not select them using evaluation results.

The provenance file has exactly source,license,citation,rights_basis, each a nonblank string of at most1,000characters. These are caller declarations, not independently verified rights or exclusive ownership. File hashes and declarations are recorded in model metadata. No external resolver is contacted. Use only data you are authorized to process; the delivered demo is original authored CC0 material.

The support-only transform uses up to10,000word/bigram/character3/4/5 TFIDF features, log1p counts and L2 normalization. Class scores aggregate the five nearest of20supports via logmeanexp(10cosine). This is a known-label supervised classifier; adding a label requires a new artifact and separate evaluation. It does not infer arbitrary categories or questions. English scope only. Candidate subsets containing2throughalltrainedclasses are supported, with uncalibrated conditional probabilities. They are not validated/calibrated accuracy claims.

Output must be a NEW directory under an existing nonsymlink parent. It is created private0700 with model.json,support.sparse,registry.json and TRAINING-RECEIPT.json, all0400. Existing output is refused. Registry publication follows binary/model validation. A filesystem error may leave a new incomplete directory; inspect it before removing it, and never activate an incomplete build. No automatic cleanup of caller files, activation, service startup or permission escalation. Same-UID hostile modification is outside this local workflow's assurance; use private directories and read-only serving artifacts.

Identical input bytes, provenance, model ID/version and implementation reproduce identical artifact/receipt bytes on the tested runtime. Input ordering is canonicalized for vector storage; an input-byte change still changes the recorded provenance hash. No timestamp or source path is embedded. Bounds also cap observed terms100,000 and stored nonzeros500,000. Token vocabulary and sparse support vectors can disclose information about the source examples even without raw utterances/IDs; no privacy guarantee. Protect artifacts accordingly.

The method does NOT become the benchmark winner by adding this CLI: the existing supervised linear profile outperformed the fixed memory recipe in the prior151-choice study. All existing fitted models are preserved. Authored demo fixtures and training tests establish mechanics/reproducibility only, not semantic quality, calibration, source independence or production readiness. Train/tune/test/calibration separation and a fresh evaluation protocol are still needed for any real dataset.

Every trained profile has disabled acceptance gates, calibration=null,review_required=true,value=null and no action authority. Nothing trains from review history or creates executable actions. Changing these gates is not supported by this command. No quality model is promoted.
