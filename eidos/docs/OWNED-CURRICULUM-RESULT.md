# Original semantic curriculum: failed learning diagnostic

The original EIDOS model was trained from scratch on a new owned synthetic corpus and failed to learn reliable entity bindings. It scored **23/72 (31.94%)** on evaluation tasks, versus **33.33% expected accuracy for uniform random choices**. Even on training examples, its top answer changed in **zero of the 160 paired tasks where gold changed**. This is a negative result, not a useful LAYA/JEV replacement or evidence that more data alone will solve the model.

## What was built and checked

The new standard-library [curriculum generator](OWNED-CURRICULUM.md) produces exact typed supervision for property lookup, entity binding, grounded negation, heavier/lighter comparison, two-link color retrieval and an explicit four-anchor mass rubric. Thirty training, six development and six evaluation worlds provide 360/72/72 tasks. Counterfactual world pairs have identical word multisets and changed bindings; each pair stays in one source group. Defaults contain **15/3/3 groups**, not 360/72/72 independent observations. Role names, literal question/context templates and exact structural assignments are disjoint within one shared synthetic ontology.

The 12 new authored methods include an independent parser of rendered contexts/questions and hand-computed constants across all six context rendering templates. All **120 decision-path unit methods passed**. The initial namespace launcher failed before tests because the restored outer sandbox mounted `/proc` read-only. The reviewed execution mode then created the same bounded offline namespaces successfully. That infrastructure failure is preserved.

Training, prediction and scoring were separate offline roles. Fit saw only training/provenance files; prediction had no gold/oracle files; scoring had no model weights. The current neural scorer/trainer/artifact/service sources were unchanged. Existing benchmark cohorts and model artifacts were not rerun. No installation, download, native reference, private repository, existing key/configuration, vendor service, GPU, paid compute or production activation occurred.

## Frozen recipe and observed result

Dimension 12, hidden 16, Adam float64, rate 0.005 and seed 113 were fixed before quality. A predeclared resource-only rule selected the largest of 6/4/2 epochs whose exact estimate fit the unchanged one-billion-work bound. Six epochs estimated **944,853,504** units; four/two estimated 672,208,128/399,562,752. There was **one fit and one complete prediction/scoring pass**, no quality-selected checkpoint, refit or retry. The final artifact has 5,060 parameters and a 64-entry training vocabulary. All outputs remain uncalibrated and review-only.

| Role | Correct / tasks | Accuracy | Uniform expected accuracy | Independent paired world groups |
| --- | --- | --- | --- | --- |
| Training |142/360|39.44%|33.33%|15|
| Development |26/72|36.11%|33.33%|3|
| Evaluation |23/72|31.94%|33.33%|3|

| Evaluation family | Correct / tasks | Uniform expected accuracy |
| --- | --- | --- |
| Property |0/12|25%|
| Binding |3/12|25%|
| Negation |6/12|50%|
| Comparison |6/12|50%|
| Two-link color retrieval |6/12|25%|
| Anchored mass score |2/12|25%|

Evaluation score expectation mean absolute error was **0.99999983 ordinal bins**. Training cross-entropy moved only from **1.16070183 to 1.13175529**. The model did not even fit the paired binding task adequately. No top answer changed in any gold-changing counterfactual pair: 0/160 training, 0/30 development and 0/30 evaluation. Chance-level marginal scores can conceal this complete failure on the binding-change diagnostic. These are finite descriptive observations, not row-IID intervals or significance claims.

## A representation limit found in source review

The current encoder averages token vectors and their first position moment before attention. In exact arithmetic, single-span contexts `a b b a .` and `b a a b .` have identical token counts and identical position sums for each spelling (a: 3, b: 3, period: 4). They therefore produce the same span representation for every setting of its learned word/byte/position parameters. The same question “Which symbol appears first?” with fixed a/b options has different truth, so this architecture cannot represent both answers correctly. This is a concrete broader ordering limitation. It does **not** prove that this curriculum's actual world pairs collide or identify the cause of all observed failures; unchanged top answers do not imply identical probability vectors. [Read-only mechanistic review](../evidence/owned-curriculum/mechanistic-source-review.json).

## Interpretation and remaining work

The compact v2 model summarizes each span independently, then performs a single question/option-conditioned attention read. It has no learned contextual token encoder or explicit multi-read relational mechanism. The earlier fixture additionally separated some pronouns/fields from their subjects; this corpus fixes that rendering issue, yet this bounded recipe still fails. The result supports prioritizing contextual representation and binding-sensitive training objectives. It does not prove an expressiveness impossibility or that an alternative optimizer/data volume could never help. No architecture or recipe was changed after revealing these outcomes.

This public-generator curriculum has a tiny English ontology and only three evaluation clusters. Wording/name shifts happen together, so failure causes cannot be isolated cleanly. Following two links on a four-cycle reaches the same destination in either direction; that slice cannot prove direction understanding or general graph reasoning. Finite label/position/length imbalances remain audited. No real-world, multilingual, long-context, many-option, calibrated-confidence or JEV/LAYA quality estimate follows. The separate historical DBpedia win and earlier 4/10 mechanics outcome describe different models/tasks and remain unchanged.

The next architecture needs to demonstrate binding-sensitive fitting before broader claims: contextualize facts/tokens, support an updated-query relational read, verify every gradient, and use fresh appropriately separated data for any adaptive follow-up. Evidence/conformal/VOI primitives cannot manufacture the missing semantic capability. No model promotion or automatic decision authority is enabled. `mission_complete=false`; `production_certified=false`.

[Full aggregate score](../evidence/owned-curriculum/score.json) · [Resource preflight](../evidence/owned-curriculum/resource-preflight.json) · [Offline execution receipts](../evidence/owned-curriculum/execution.json) · [Frozen protocol](../evidence/owned-curriculum/protocol.md).

Private model JSON SHA `5b6a734366fb8172481192d9e51bd571f652ed3b3a3509ec362ef5283cde50d1`. Training receipt records 10.374126 CPU / 10.487297 elapsed seconds. Per-role controls were 768 MiB address space and 600 CPU/900 elapsed seconds for fitting, 120 CPU/180 elapsed seconds otherwise; these are per-process controls plus parent watchdogs, not an aggregate cgroup guarantee. Waited-wrapper CPU excludes traced descendants. Every role had one refused route probe and zero unexpected external IP connections. Raw generated tasks, gold, per-case predictions and weights are not published.
