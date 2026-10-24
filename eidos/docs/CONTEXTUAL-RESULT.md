# Contextual model: learns ordering, fails reliable relational transfer

EIDOS v3.1 is an original runnable context/question/candidate decision model, trainer, checked artifact loader, CLI and signed local API/browser adapter. It uses only standard-library Python and no pretrained checkpoint. Its first frozen corrected experiment **did not reach the predeclared 95% target in any of four held-out slices**. LAYA/JEV were not accessed or compared. This is a research candidate, not a replacement or production release.

## Measured outcome

| Role | Correct | Accuracy | Uniform expected | NLL | Brier | Paired source groups |
|---|---:|---:|---:|---:|---:|---:|
| training | 64/72 | 88.89% | 29.26% | 0.2661 | 0.1603 | 8 |
| fresh_world | 20/36 | 55.56% | 29.26% | 2.2428 | 0.6909 | 4 |
| novel_entities | 24/36 | 66.67% | 29.26% | 0.6799 | 0.4194 | 4 |
| new_wording | 12/36 | 33.33% | 29.26% | 2.2880 | 0.9371 | 4 |
| joint | 7/36 | 19.44% | 29.26% | 2.6641 | 1.1281 | 4 |

Uniform is the expected accuracy of randomized choice, not a trained rival. NLL/Brier expose poor probability quality; confidence is not calibrated. The four test slices total 63/144 correct (43.75%), but this aggregate does not replace the requirement to reach at least 95% in EACH slice. Only four paired source groups occur in each slice; rows are not IID. No statistical interval or real-domain accuracy claim follows.

| Family | Training | Fresh world | Novel entities | New wording | Joint |
|---|---:|---:|---:|---:|---:|
| property | 16/16 | 3/8 | 8/8 | 2/8 | 0/8 |
| binding | 13/16 | 3/8 | 5/8 | 0/8 | 3/8 |
| one_hop | 15/16 | 6/8 | 5/8 | 3/8 | 0/8 |
| two_hop | 12/16 | 4/8 | 2/8 | 3/8 | 0/8 |
| sequence_first | 8/8 | 4/4 | 4/4 | 4/4 | 4/4 |

Ordering succeeds on all 16 held-out first-symbol cases. This narrow shared-pattern regression is not general language understanding. The joint slice has zero correct property, one-hop and two-hop answers; inverse binding is 3/8. New-wording binding is 0/8. Training is only 64/72, so fitting and transfer both remain incomplete.

| Role | Changed-gold pairs | Top answer changed | Both variants correct |
|---|---:|---:|---:|
| training | 36 | 30 | 29 |
| fresh_world | 18 | 9 | 6 |
| novel_entities | 18 | 12 | 9 |
| new_wording | 18 | 6 | 2 |
| joint | 18 | 6 | 3 |

Answer changes show sensitivity, not guaranteed correctness. No same-cohort refit, checkpoint selection or repeated scoring followed. Earlier v2 negative outcomes remain in [the owned-curriculum report](OWNED-CURRICULUM-RESULT.md); earlier fixed-class benchmark results concern different artifacts and cannot be transferred here.

## What changed and how it was verified

The encoder applies learned nonlinear position/neighbor contextualization to context, question and option text. Three query-updated reads carry exact normalized-spelling key/value pointers through facts. A shared learned head scores every option and produces a full softmax. V3.1 explicitly exposes candidate spelling's key and copied-value mass at each read, including the last; IDs and numeric anchors are not semantic features. This is generic memory/copy machinery, with no hue/graph task parser in inference.

The initial v3 draft completed training but was never predicted or scored. Source algebra exposed an unconditional failure: unknown `abba`/`baab` options under `abba baab baab abba .` have equal embeddings/counts, and the head had no candidate-specific pointer readout. V3.1 repairs that missing path, uses distinct architecture/model identifiers and refuses old weights. Initial sources, training and the proof remain retained. The repair was motivated by this data-independent proof, not a held-out result.

169 authored unit methods passed, including an independent scalar forward implementation, all-coordinate finite-difference gradients on a tiny model, order/unknown-name readout witnesses, contract bounds, artifact tamper refusal and transport checks. Twelve actual tiny training/CLI/API checks passed. Nine additional checks loaded the actual fitted artifact through CLI and signed loopback HTTP for CHOICE/BINARY/SCORE, including auth/replay/pins/assets. These establish mechanics, not binary/score semantic quality. No new real Chromium interaction was measured; earlier Chromium results apply to the old artifact.

Pinned REA (`e09a503f44fba1639b71907a018d71161360f5ae`) passively inspected 58 source/document/control/verification members BEFORE the corrected fit: complete entry coverage, 59 graph nodes, every hash/size matched, no contradictions, no target installation/import/execution. Later result/delivery files are outside that packet. REA establishes provenance and structure here; it does not certify security, understanding, accuracy or ownership. [REA review](../evidence/contextual/rea-integrity-review.json).

## Fixed recipe and isolation

The unchanged authored generator selected seed 37279, the first qualifying seed in 37199..37326 (81 candidates), using only disjoint prior relation-structure/sequence-group hashes. No model or quality selected the data. Training contains 72 CHOICE rows from eight paired groups; each held-out slice has 36 from four groups. Public ontology, ordering pattern and some controlled name/grammar regimes remain shared. Exact group exclusion is not language, semantic, pretraining or independent-real-source separation.

Fixed D=8/H=12, Adam float64, rate .005, seed 113 and final checkpoint. Training-only preflight selected 16 epochs from 16/8/4/2/1 using 566,539,776 estimated work units under the unchanged 1b limit. There are 1,126 parameters and 48 training vocabulary entries. Training took 47.1454 CPU / 47.2387 elapsed seconds; 1,152 optimizer steps reduced training cross-entropy from 1.282951 to 0.266107. This is not measured end-to-end throughput or a FLOP guarantee.

Model ID `owned-contextual-memory-v3p1`; model JSON SHA `c7ccf74fefe48a1d8439dd7a44c64dea59eca5685e167c9a83255fcfdd7a2b51`; registry SHA `7ac358b4de0ab56d4b0ccc461c72c1db75f8b693ab40104f34f9ce0b55515bcd`. Raw rows, labels, per-case predictions, vocabulary and weights remain outside Git. Original synthetic artifacts are retained privately.

Eight separate offline source-pinned roles completed with cleared environments, readonly role inputs, one refused route probe each and zero unexpected external IP connections. Training sees only training/provenance; prediction sees requests/model without gold; scoring sees gold/predictions without weights. Per-process resource limits and watchdogs are not aggregate cgroup guarantees. No installations/downloads, real reference/corpus, existing credential/config, private repo, TypeSafe/JEV service, GPU, paid compute or promotion occurred. Fixture keys were freshly created then deleted, and all test services stopped.

## Remaining technical work

The missing readout is fixed, but one experiment cannot isolate the remaining causes. Testable concerns are shallow local language encoding, unknown wording without semantic supervision, a single key/value convention shared across fact types and weak question-specific control of read direction/depth. Additional training alone is not established as a solution. Next development should first use training-only microtasks to test explicit relation/direction/read-depth control and language-paraphrase supervision; freeze a new architecture and separate exposure-disjoint challenge before any quality measurement. Preserve this failed attempt. No broad 95% promise can be inferred from one repaired capacity witness.

Current bounds: 512 context tokens, 64 spans, 128 prompt tokens, 64 tokens per option and 2,048 total option tokens plus work/tape limits. The 255-option mechanics request was explicitly budget-refused; full 255-option support and long-context quality are unproven. Unknown names preserve identity, not unknown meanings. Every accepted input is fully processed; exceeding bounds is refused, never silently truncated.

All probabilities are conditional on offered candidates, uncalibrated and review-only (`value=null`, no action authority). The loopback development server still needs production identity/TLS, persistent replay/recovery, load/drift and promotion qualification. Calibration, independent natural-domain tasks and a valid new LAYA/JEV comparison remain open. Original implementation/data lineage does not certify exclusive legal rights; retained third-party dataset notices apply to earlier separate research materials. No new blanket public source license is granted. `mission_complete=false`; `production_certified=false`.

[Run/train guide](CONTEXTUAL-MODEL.md) · [Complete aggregate scores](../evidence/contextual/score.json) · [Frozen protocol](../evidence/contextual/PROTOCOL.md) · [Completion checks](../evidence/contextual/completion-review.json).
