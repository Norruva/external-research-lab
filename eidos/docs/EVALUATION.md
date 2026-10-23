# Recorded classifier comparisons

## Newer DBpedia14 comparison

The separately approved 280-case comparison tied accuracy at **243/280 (86.79%)** for both EIDOS and public LAYA. EIDOS macro-F1 was **86.66%**, versus **84.22%**, but both predefined individual interval gates failed. This result does not meet the requirement to beat LAYA. The primary was the actual owned stdlib Adam trainer at fixed 12 epochs, selected only on development data, with 20 supports/class versus zero-shot LAYA. All 14 choices and full articles were preserved. No post-test candidate substitution or promotion.

[Full DBpedia result, resource/data scope and uncertainty](DBPEDIA-RESULT.md) · [Authoritative aggregate score](../evidence/dbpedia-study/score.json). The current test outcomes are revealed and consumed; future changes require newly planned evaluation.

## Earlier SNIPS-derived comparison

The approved public LAYA comparison completed once on 1,821 SNIPS-derived intent cases with seven choices. The cases belonged to 1,409 template/text components absent from an earlier 140-case pilot. All 373 rows belonging to the pilot components were excluded. No raw cases, gold answers or per-case predictions are published here.

| Model | Correct | Accuracy | Macro-F1, fixed seven labels |
| --- | ---: | ---: | ---: |
| EIDOS linear, fixed primary | 1715 / 1821 | 94.1790% | 93.0034% |
| EIDOS support-memory, secondary | 1708 / 1821 | 93.7946% | 92.1196% |
| Public LAYA, direct seven-choice | 1684 / 1821 | 92.4767% | 89.9720% |

Primary EIDOS minus LAYA accuracy difference: **+1.7024 percentage points**, individual approximate 95% paired component-bootstrap interval **[+0.2225, +3.2327]** points. Macro-F1 difference: **+3.0314 points**, interval **[+1.0483, +5.1051]**. Both predeclared cohort-scoped numeric gates passed. Memory's intervals crossed zero and its gates failed; it was not substituted for the primary.

The comparison is exploratory. Its follow-up plan was made after seeing pilot outcomes, and EIDOS's original evaluation outputs were already revealed. EIDOS used labeled supports while LAYA was zero-shot. The 2,000 paired, within-gold-class component-bootstrap draws assume unverified group exchangeability. These individual intervals do not provide joint guarantees, global control across the project's experiments, verified speaker/source/paraphrase independence, or confirmatory replication. No 100% accuracy, strongest-LAYA, general semantic, multilingual, unseen-label, JEV or production claim is supported.

LAYA used its public direct choice path with all seven choices together, fixed descriptions/state/question, eager float32, one thread, no shortlist/tournament and no lost options/state tokens. Public source commit `ca5045a3f1fac06fc6daa5cf3371110f322f5255` and checkpoint `convaiinnovations/laya@1c5edc17a7acd8701df6fc341c0d179f1c62c982` are pinned in the receipts. Weight SHA-256: `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`. No LAYA implementation or tensors ship in EIDOS.

Native prediction used 1,233.10 CPU seconds; scoring used 9.25; whole elapsed was 1,273.12 seconds. CPU excludes wrappers/unwaited descendants; per-process limits and reserves are not an aggregate cgroup guarantee. Gold/peer outputs were absent from prediction; source texts/tensors/dependencies absent from scoring. No reference run, bootstrap calculation, model fitting or quality scoring was repeated for this GitHub delivery. The completed reference attempt is consumed; its scripts are historical inspection material with absolute workspace paths.

The [aggregate score](../evidence/comparison/evaluation/production/support-remaining-reference-v1/v1/score/data/result.json), [independent aggregate review](../evidence/remaining-review/independent-aggregate-review-v1.json), [protocol](../evidence/comparison/research/support-remaining-reference-v1/PROTOCOL.md), and [frozen report snapshot](../evidence/comparison/research/41-remaining-laya-and-owned-training.md) preserve details. The snapshot retains historical workspace links, some referring to omitted binaries/files; use this document and the evidence index when browsing this source checkout.

The earlier 140-case pilot had higher observed primary EIDOS performance but both interval gates failed. Its [aggregate score](../evidence/comparison/evaluation/production/support-reference-v1/v1/score/data/result.json) is retained separately. Earlier support-memory versus linear results were negative with 151 choices; this source's own-data memory trainer is not the model that produced the primary linear score. Revealed evaluations must not be reused to select later recipes or called fresh holdouts.

REA passively inspected the 35-member owned-training source kit and the separate 32-member aggregate comparison packet. Independent reviews matched every member hash/size with complete coverage and no contradictions. This establishes inspected byte identities, not classifier quality, security or rights. [REA evidence](../evidence/README.md).

Dataset provenance: SNIPS `b86ac7f1577868c42158d0dec77db50956046696`, CC0-1.0, Coucke et al., Snips Voice Platform, 2018, arXiv 1805.10190. Intent labels are derived from file membership rather than the publisher's slot-filling task. CLINC support training uses `828f8093932c8fe6ca7936c3d2e52903b1c523de`, CC BY 3.0, Larson et al., EMNLP-IJCNLP 2019, D19-1131. Dataset authors do not endorse these results, and public licensed data are not exclusively owned.

The new [own-data linear command](LINEAR-TRAINING.md) trains a fresh, zero-initialized full-batch softmax model using the standard library. It is a different recipe from the frozen Adam-trained primary artifact above. Its authored numerical/unit and real-service checks do not transfer this benchmark accuracy to newly trained models. No historical benchmark was rerun for that feature.

The command also offers an original bias-corrected `--optimizer adam` option. Its standard-library float64 fitting and fixed final epoch differ from the historical float32, development-selected recipe. The optimizer's numerical, artifact-regression, serving and evaluation-workflow checks use authored fixtures and create no new LAYA/JEV or general-quality result.

[Owned evaluation](OWNED-EVALUATION.md) now provides a separate versioned prepare/predict/score workflow for new caller-trained registries. Its arithmetic and file-role tests use authored fixtures; no historical comparison was repeated. It does not prove that caller data are untouched or independent, and its descriptive metrics do not create a new LAYA/JEV result.

[Owned paired comparison](OWNED-COMPARISON.md) adds reusable protocol binding and whole-group resampling for two newly evaluated owned models. It verifies complete paired scope and reports individual approximate accuracy/macro-F1 difference intervals; no LAYA/JEV importer is implemented in that command. Its authored arithmetic and isolated CLI checks do not rescore the preserved comparison above or provide new rival evidence.

JEV remains unmeasured. TypeSafe/JEV service access and model extraction are not authorized. Production certification, independent matched-adaptation cohorts, operational calibration/OOS risk, identity/TLS/security, drift/load operations and recovery objectives remain unresolved.
