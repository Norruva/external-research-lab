# Original EIDOS decision-model build and critical results

EIDOS now has an original trainable context/question/candidate scorer, not only a typed wrapper over a fixed class classifier. The new local library, CLI, signed loopback API and browser return structured CHOICE, BINARY and anchored SCORE distributions from caller-supplied question/answer meanings. No LAYA/JEV implementation, pretrained checkpoint, vendor outputs or third-party Python training/inference package is included. Commodity Python/SQLite/OpenSSL/OS/browser licenses still apply to those components. No blanket public license grant has been applied to the original EIDOS source.

## Working implementation

Architecture v2 combines learned word and UTF-8 byte vectors with within-token and sequence position moments, global context-span position, question/candidate-conditioned attention and a shared nonlinear candidate function. Its from-scratch Adam trainer learns on caller-owned tasks rather than class IDs. Candidate identities/numeric anchors supply no semantic features. All offered logits compete in one softmax. Every accepted context is accounted for or explicitly refused. This is a compact moment/attention model, not a contextual foundation encoder. Different strings may still share summary features; preserving some order and spelling does not establish general compositional understanding.

The final authored artifact has 5,672 parameters, 115 training-vocabulary entries, dimension 12 / hidden 16, and a separate learned 256-byte table. It uses 144 original visible training tasks, 10 fixed epochs, seed 113 and rate 0.005, with float64 fitting/inference. Twenty epochs exceeded the unchanged work bound before fitting (1,382,324,736 versus 1 billion); ten fits 724,294,656. The later learning-rate choice used only a predeclared training-loss comparison of 0.02 / 0.005 / 0.001, with smoke/test data unmounted. Final training CE 0.32118 is a fitting diagnostic, not an accuracy estimate.

Private model JSON SHA `ca5c53725d902fc087d9ee2939c5e7afa8656ee0779ebb4953e379cd6b393eec`; registry SHA `4336ea9c095387ce058ae233a17eecf4aef41c8ab6877d227e4e509b762bed58`. No model weights are published in Git. The separately packaged owned demonstration release can include those explicitly authored-fixture artifacts.

## Verification and what failed

| Check | Recorded result | Meaning |
| --- | --- | --- |
| New authored unit methods |108 passed |All analytical gradient coordinates against independent finite differences, scalar forward oracle, order/spelling/candidate behavior, training supervision, artifact/refusal and HTTP checks|
| Final actual training/CLI/API integration |12 passed |Three typed heads, complete255-option mechanics, library/CLI/signedHTTP parity, evidence assessment, replay refusal, cleanup|
| Real Chromium UI |13 passed |WebCrypto signing, pinned identity, three typed outputs,255display, malformedJSON refusal, key/session clearing and reload|
| Final visible choice/binary smoke outcomes |4/10correct|Too weak to claim a useful semantic replacement; small shared-template visible fixtures are not an independent benchmark|
| Final visible score absolute errors |0.0447315and0.990492|Two authored ordinal mechanics cases; not rubric-quality evidence|

The first word-only architecture also returned4/10 on the visible choice/binary cases. Its v2 representation successor at the initial 0.02 rate returned 1/10. All these outcomes are retained. Architectural corrections were motivated by source-level information loss, and the rate diagnostic used training loss only. The final4/10 does not demonstrate an accuracy improvement over the first architecture, general language competence, calibration, multilingual ability, long-context reasoning or LAYA/JEV parity. The original144 training / 12 smoke tasks share finite templates and are visible; no pristine quality holdout is claimed.

The previous fixed 14-class DBpedia artifact's 95.50% result remains separate, unchanged and scoped to its already consumed evaluation. It is not a score for this new question-conditioned model. No prior real benchmark, LAYA checkpoint, dataset reader, old model or proprietary service was rerun.

## Applied parts of the supplied mathematical direction

Original finite components now preserve four-valued evidence (neither/positive/negative/contradictory support), construct conservative split-conformal candidate sets from caller-supplied calibration scores, and calculate one-step value of information from a complete finite joint/loss/cost model. Twenty-three of the108 checks test these operations with hand-calculated and exact rational fixtures. Actual forecast assessment is exercised by the end-to-end workflow.

Evidence flags remain caller assertions. Conformal coverage requires suitable fixed-score, disjoint-calibration and exchangeability premises and an applicable model/population/candidate regime; the primitives mark these bindings unverified. No real classifier calibration was fitted. Positive information value does not authorize a tool call. The implementation does not claim a solved POMDP, causal identification, a formal proof of world truth, or a verified intelligence operating system. These controls do not manufacture semantic competence or classifier accuracy.

## Boundaries and reproducibility

All validation roles ran in private mount/PID/network namespaces, read-only staged source, cleared environments and explicit per-process CPU/elapsed controls. Model/test roles had768 MiB address-space caps. Browser used a 150-second outer watchdog and 120 CPU seconds per process, with no browser address-space guarantee because Chromium reserves large virtual regions; no aggregate cgroup bound is claimed. Waited-wrapper CPU statistics exclude some traced descendants; the model's own receipt records 7.97713CPU/8.06191elapsed seconds for training. Whole final integration took 8.87465 seconds.

The final units/integration had only refused route probes and loopback traffic. Chromium made one standard IPv6 reachability probe, denied ENETUNREACH inside the private network. The original one-line trace parser flagged its unfinished call prematurely; the preserved paired-call review confirms no unexpected external IP connection and no pending call. All13 browser checks completed and browser/service/fresh private config/profile were removed. No existing credential, raw CDP/key payload, private repository, production service, installation, download, GPU or paid compute was accessed.

All 84 original imported source files and the27 prior core-package files remain unchanged. Seven new core modules implement the new path. Current source matches the exact final unit/integration snapshots. Dataset notices stay in research provenance; no public benchmark corpus or old model is silently presented as owned production data. The uploaded architectural note itself is not republished.

[Run/train locally](DECISION-MODEL.md) · [Task and ownership format](DECISION-DATA.md) · [Evidence mathematics](DECISION-EPISTEMIC.md) · [Aggregate execution evidence](../evidence/question-decision/README.md).

The remaining central gap is an adequate owned multi-task semantic training corpus and independent task/domain/option-family evaluation, alongside a stronger representation if the compact model cannot learn that scope. Fresh calibration/OOS, source-bound evidence verification and production identity/TLS/encrypted recovery/load/drift/promotion checks remain open. All forecasts remain uncalibrated and review-only (`value=null`, no action authority). `mission_complete=false`; `production_certified=false`.
