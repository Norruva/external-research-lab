# Routed model: question-controlled memory experiment

EIDOS v4 is an original runnable context/question/option decision model with an owned-data trainer, strict separate artifact loader, CLI and signed local API/browser adapter. The single frozen experiment **FAILED the predeclared 95% milestone**. This is adaptive synthetic mechanism research, not an independent natural-domain benchmark or evidence of LAYA/JEV superiority. No LAYA/JEV service or model was used.

## Complete diagnostic outcome

| Role | Correct | Accuracy | Uniform expected | NLL | Brier | Paired groups | 95% gate |
|---|---:|---:|---:|---:|---:|---:|---|
| training | 52/128 | 40.62% | 20.00% | 1.3283 | 0.6670 | 4 | training only |
| fresh_world | 16/32 | 50.00% | 20.00% | 1.4538 | 0.7174 | 2 | FAIL |
| novel_entities | 9/32 | 28.12% | 20.00% | 1.4488 | 0.7147 | 2 | FAIL |
| new_composition | 12/32 | 37.50% | 20.00% | 1.6319 | 0.7961 | 2 | FAIL |
| joint | 10/32 | 31.25% | 20.00% | 1.5375 | 0.7649 | 2 | FAIL |

Uniform is analytic randomized choice, not a trained rival. The gate requires at least 95% in EACH held-out slice (at least 31/32), not a pooled score. There are only four training paired worlds and two per held-out slice; rows are correlated. No row-IID interval or statistical superiority is claimed.

| Family | Training | Fresh world | Novel names | New composition | Joint |
|---|---:|---:|---:|---:|---:|
| binding | 7/16 | 0/4 | 0/4 | 0/4 | 1/4 |
| cites_forward_one | 10/16 | 2/4 | 2/4 | 2/4 | 1/4 |
| cites_forward_two | 0/16 | 4/4 | 0/4 | 2/4 | 0/4 |
| cites_incoming_one | 4/16 | 0/4 | 1/4 | 1/4 | 2/4 |
| points_forward_one | 5/16 | 2/4 | 1/4 | 1/4 | 0/4 |
| points_forward_two | 4/16 | 2/4 | 1/4 | 0/4 | 1/4 |
| points_incoming_one | 6/16 | 2/4 | 0/4 | 2/4 | 1/4 |
| property | 16/16 | 4/4 | 4/4 | 4/4 | 4/4 |

| Role | Changed-gold pairs | Top answer changed | Both variants correct |
|---|---:|---:|---:|
| training | 64 | 52 | 20 |
| fresh_world | 16 | 14 | 8 |
| novel_entities | 16 | 14 | 2 |
| new_composition | 16 | 14 | 5 |
| joint | 16 | 2 | 2 |

| Role | Seven-question contrast sets | All seven correct | Any top-answer variation |
|---|---:|---:|---:|
| training | 16 | 0 | 16 |
| fresh_world | 4 | 0 | 4 |
| novel_entities | 4 | 0 | 3 |
| new_composition | 4 | 0 | 4 |
| joint | 4 | 0 | 4 |

The seven questions hold context, subject and semantic candidate set fixed while varying property, relation, direction and depth. All-seven-correct is stronger evidence of question control than any answer change. IDs/order are randomized; ties can make the scorer’s ID-based top-answer selection vary without semantic control. Therefore any-variation counts alone do not prove routing, causal reasoning or correctness. All disclosed poor family, pair and contrast outcomes are retained. No quality-selected refit or repeated scoring followed.

## Original architecture and supervision

The nonlinear token/neighbor encoder adds original deterministic spelling/subword features to trainable embeddings. Exact spelling pointers preserve unknown-name identity. Each of three reads has question-conditioned key/value roles and positional terms, plus learned relation/direction control; a question-conditioned depth mixture feeds a direct copied-mass logit and shared residual scorer. Every offered option is scored or the request is refused. There is no task-specific graph/hue parser in inference and no pretrained language meaning.

The new curriculum has two named relations and original five-node worlds. Training includes operator lexemes and multiple wording forms before held-out composition. **New composition of known lexemes differs from v3.1’s unknown-synonym shift**. This is a changed task/cohort, so its accuracy cannot be reported as a measured same-test gain over v3.1. Hash exclusion protects specified exact structures, not semantic, language or graph-isomorphism independence. Unknown spelling/subword similarity does not supply the meaning of an unseen synonym.

## Verification and resource history

223 authored unit methods passed in `units-v4`, including independent scalar forward/all-coordinate gradients, nondegenerate question-routing witnesses, fused primitive derivative checks, version isolation, bounds and transport. 12 actual tiny fixture training/CLI/API checks and 9 actual frozen-artifact CLI/signed-HTTP checks passed. CHOICE/BINARY/SCORE mechanics do not validate binary/score semantic accuracy. No new real Chromium interaction was measured; previous browser results apply to older artifacts. The initial hand-oracle roundoff assertion failure and its corrected verification history remain retained; it did not trigger a scientific fit or quality-selected change.

The initial training-only preflight refused the original 4m request-work estimate before any scientific fit/quality observation. Source-only revision 68 raised v4 request-work to 5m, tape to 500k nodes and training work to 12b, serializing strict resource metadata. CPU600, wall900 seconds and memory768MiB per process stayed fixed. Initial sources, REA evidence and refusal remain retained. Revision 70 fused sums/dots/diagonal contractions in the original autodiff backend while preserving ordered forward arithmetic and analytical derivatives; it changed implementation overhead, not equations, parameters or data.

| Training-only probe | Gradient CPU s | Gradient elapsed s | Tape nodes | Peak RSS KiB | Optimizer steps |
|---|---:|---:|---:|---:|---:|
| before fused AD | 0.749974 | 0.795209 | 342982 | 115040 | 0 |
| after fused AD | 0.316784 | 0.325152 | 77038 | 64812 | 0 |

Both probes used the highest-estimated-work TRAINING request and arbitrary target0; loss/gradient/probability values were discarded, with no optimizer, accuracy, held-out input or quality-based choice. The frozen resource rule selected the largest of16/8/4/2/1 epochs fitting12b work and `2 * probe_gradient_CPU * rows * epochs + setup_CPU <= 540`, with analogous810s wall reserve. These conservative estimates are not a cgroup or latency guarantee.

Selected **4 epochs**, D8/H12, Adam float64, rate0.005, seed113, fixed final checkpoint; 1434 parameters, 44 training vocabulary entries, 2,481,401,856 estimated work units. Training: 112.0315CPU / 112.2724elapsed seconds, 512 optimizer steps; training cross-entropy 1.614095 → 1.328282. This is not measured production throughput.

Model `owned-question-routed-v4`, SHA `16e122d03109e9a155f6bff7ca2911eaa64ebcc838881b977f90947909d7b608`; registry SHA `e9ffcf05fde6d166154e08f4d55c3f68c9bc36c265698f25d56b413e28f39014`. Raw rows/gold/per-case predictions, vocabulary and weights remain private. Fit sees only training/provenance; prediction sees model/requests without gold; scoring sees frozen predictions/gold without weights. Temporary fixture keys were new and deleted, and services stopped. Per-process caps/watchdogs are not aggregate cgroups.

Pinned REA (`e09a503f44fba1639b71907a018d71161360f5ae`) passively inspected the final 74-member prefit source/control/verification packet: complete entry coverage, 75 graph nodes, all hashes/sizes match, no contradiction or target installation/import/execution. Earlier packets remain retained. Results and later delivery metadata lie outside that source packet. REA records byte identity/structure, not accuracy, security, semantic understanding, ownership or production certification.

## Limits and remaining work

Six original modules preserve all41 previous package payloads and84 imported members. No external training/inference ML runtime, pretrained checkpoint, installation/download, private repository, existing credential/configuration, TypeSafe service, GPU or paid compute was used. Original source lineage and retained dataset notices are not an exclusive rights certificate; no new blanket public source license is granted.

Bounds remain512 context tokens,64 spans,128 prompt tokens,64 tokens per option and2048 total option tokens, plus5m request-work and500k tape nodes. The255-option mechanics request was explicitly budget-refused; full255 capability is unproven. Accepted inputs are fully processed, never silently truncated; long-context quality is unproven.

Further causes remain hypotheses: finite language encoding, learned role/depth ambiguity, optimization/data coverage and context positional shortcuts. One adaptive experiment does not identify their causal contributions. Fresh independent natural-domain tasks, adequate owned multi-task supervision, calibration/OOS/error-coverage validation and an appropriate new public LAYA comparison remain necessary. JEV is unmeasured and its service/extraction is unauthorized. All probabilities stay uncalibrated and conditional on offered choices; `review_required=true`, `value=null`, zero accepted coverage and no action authority. Production identity/TLS, CPU interruption, durable replay/recovery, load/drift/security and promotion qualification remain open. No merge or production deployment follows. `mission_complete=false`; `production_certified=false`.

[Run/train guide](ROUTED-MODEL.md) · [Complete aggregate scores](../evidence/routed/score.json).
